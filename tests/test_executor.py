"""The thread that runs a Web-triggered job, and the log plumbing around it.

`run_job_in_thread` decides the status every job detail page shows, attaches a
log handler to the **root** logger for the duration of the run, and must detach
it again. The pipeline runs six stages in parallel, so the handler's thread
filter is what keeps one job's log stream from filling with another's lines --
and a handler left attached would keep writing into a dead LogBus for the life
of the process.
"""

from __future__ import annotations

import logging
import threading

import pytest

from web.runner import executor
from web.runner.handler import LogBusHandler
from web.runner.registry import RunningTask, TaskRegistry


class _Bus:
    def __init__(self):
        self.events: list[dict] = []

    def write(self, event):
        self.events.append(event)


def _task(job_name="research/demo", started_by="admin") -> RunningTask:
    return RunningTask(
        task_id="t1",
        job_name=job_name,
        started_by=started_by,
        cancel_token=threading.Event(),
        log_bus=_Bus(),
        engine=None,
    )


@pytest.fixture(autouse=True)
def _solo_registry():
    registry = TaskRegistry()
    with registry._lock:
        registry._tasks.clear()
    yield
    with registry._lock:
        registry._tasks.clear()


# --- progress event mapping --------------------------------------------------

def test_a_terminal_event_keeps_its_type():
    # The SSE stream ends a run by seeing type=status; re-wrapping it would
    # leave the client waiting for a completion that never arrives.
    for kind in ("status", "error"):
        event = {"type": kind, "status": "success"}
        assert executor.wrap_progress_event(event) == event


def test_a_non_terminal_event_is_wrapped_as_progress():
    # The engine's own "type" would otherwise overwrite the envelope and the UI
    # could not recognise the line at all.
    wrapped = executor.wrap_progress_event(
        {"type": "phase", "phase": "researching", "elapsed": 15})
    assert wrapped["type"] == "progress"
    assert wrapped["event_type"] == "phase"
    assert wrapped["phase"] == "researching"
    assert wrapped["elapsed"] == 15


def test_a_round_event_keeps_its_own_discriminator():
    wrapped = executor.wrap_progress_event(
        {"type": "round", "round": 3, "max_rounds": 20})
    assert wrapped["event_type"] == "round"
    assert wrapped["round"] == 3


def test_the_original_event_is_not_mutated():
    event = {"type": "phase", "phase": "searching"}
    executor.wrap_progress_event(event)
    assert event == {"type": "phase", "phase": "searching"}


# --- status transitions ------------------------------------------------------

def _run(task, monkeypatch, result=None, raises=None, cancelled=False):
    class _Engine:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def run_job(self, name, verbose=False):
            if raises is not None:
                raise raises
            return result

    monkeypatch.setattr(executor, "ResearchEngine", _Engine)
    if cancelled:
        task.cancel_token.set()
    executor.run_job_in_thread(task, config_dir="config", jobs_dir="jobs")
    return task


def test_a_successful_run_is_marked_success(monkeypatch):
    task = _run(_task(), monkeypatch, result="output/x.md")
    assert task.status == "success"
    assert task.log_bus.events[-1] == {"type": "status", "status": "success"}


def test_a_run_that_produced_no_path_is_a_failure(monkeypatch):
    # run_job returns None for a failure; treating that as success would show a
    # green tick on a job that wrote nothing.
    task = _run(_task(), monkeypatch, result=None)
    assert task.status == "failed"


def test_a_cancelled_token_wins_over_a_returned_path(monkeypatch):
    # The engine can finish writing after the user hit stop; the user's
    # intention is what the page should report.
    task = _run(_task(), monkeypatch, result="output/x.md", cancelled=True)
    assert task.status == "cancelled"


def test_an_unhandled_exception_is_contained_and_reported(monkeypatch):
    task = _run(_task(), monkeypatch, raises=RuntimeError("boom"))
    assert task.status == "failed"
    assert any(e.get("type") == "error" for e in task.log_bus.events)


def test_the_starting_user_reaches_the_engine(monkeypatch):
    seen = {}

    class _Engine:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def run_job(self, name, verbose=False):
            return "output/x.md"

    monkeypatch.setattr(executor, "ResearchEngine", _Engine)
    executor.run_job_in_thread(_task(started_by="editor"), "config", "jobs")
    assert seen["user"] == "editor"


# --- the log handler lifecycle ----------------------------------------------

def test_the_handler_is_removed_even_when_the_run_explodes(monkeypatch):
    before = list(logging.getLogger().handlers)
    task = _run(_task(), monkeypatch, raises=RuntimeError("boom"))
    assert list(logging.getLogger().handlers) == before, \
        "a handler left on the root logger keeps writing into a dead LogBus"


def test_only_this_thread_s_own_records_reach_the_bus():
    """Six radars run at once, each with a handler on the root logger.

    Without the thread filter every stage's live log would show every other
    stage's lines, and a synthesis stage reading its log would be reading four
    unrelated runs.
    """
    mine, theirs = _Bus(), _Bus()
    handler = LogBusHandler(mine)
    handler.setFormatter(logging.Formatter("%(message)s"))

    def emit(bus_thread, bus):
        record = logging.LogRecord("x", logging.INFO, "f", 1, "hello", None, None)
        record.thread = bus_thread
        # Route it through the same handler the executor installs.
        logging.getLogger().handle(record)
        return bus

    main = threading.get_ident()
    other = main + 1
    logging.getLogger().addHandler(handler)
    try:
        emit(other, theirs)
        assert mine.events == [], "another thread's record leaked into this bus"
        emit(main, mine)
        assert len(mine.events) == 1
    finally:
        logging.getLogger().removeHandler(handler)
