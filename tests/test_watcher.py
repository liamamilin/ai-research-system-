"""Incremental report indexing watcher.

The watcher compared ``change_type.value`` against the strings
``"added"``/``"modified"``, but ``watchfiles.Change`` is an enum whose
``.value`` is an **int**. The comparison never matched, so new reports were
never indexed until a restart (and never reached the vector store).
"""

from __future__ import annotations

import os
import threading
import time

import pytest

watchfiles = pytest.importorskip("watchfiles")


@pytest.fixture()
def watcher_env(tmp_path, monkeypatch):
    """Isolated output dir + reports DB, with watchers stopped before teardown.

    Depends on monkeypatch so this fixture is finalized *before* monkeypatch
    undoes the DB path override — otherwise a lingering watcher thread would
    write into the next test's database (or the real one).
    """
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    state = tmp_path / "state"
    output = tmp_path / "output"
    state.mkdir()
    output.mkdir()
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(state / "reports.db"))
    index_db.init_db()
    vector_sync.reset_client()
    monkeypatch.setattr(vector_sync, "_embedding_client", lambda: None)

    started: list[tuple[threading.Event, object]] = []
    out_dir = output
    state_dir = state

    class Env:
        output = out_dir
        state = state_dir

        @staticmethod
        def start(watcher, **kwargs):
            stop = threading.Event()
            thread = watcher.start_watcher(str(out_dir), stop_event=stop, **kwargs)
            started.append((stop, thread))
            return thread

    try:
        yield Env()
    finally:
        for stop, _thread in started:
            stop.set()
        for _stop, thread in started:
            thread.join(timeout=5)
        vector_sync.reset_client()


def _write(output, rel, text="# Title\n\nbody about steam and indie games\n"):
    path = os.path.join(str(output), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_change_enum_values_are_not_strings():
    """Guard the exact bug: comparing .value to strings silently matches nothing."""
    assert watchfiles.Change.added.value != "added"
    assert watchfiles.Change.modified.value != "modified"
    assert watchfiles.Change.deleted.value != "deleted"


def test_watcher_indexes_new_report(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import watcher

    output = str(watcher_env.output)
    calls: list[int] = []
    watcher_env.start(watcher, poll_delay_ms=100, on_change=lambda: calls.append(1))
    time.sleep(0.8)

    _write(output, "research/new.md")
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 1, 8)
    rows = index_db.list_reports(page=1, per_page=10)["items"]
    assert rows[0]["path"] == "research/new.md"
    assert calls, "on_change callback should have fired"


def test_watcher_updates_modified_report(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import watcher

    output = str(watcher_env.output)
    _write(output, "research/a.md")
    watcher_env.start(watcher, poll_delay_ms=100)
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 1, 8)

    time.sleep(0.3)
    _write(output, "research/a.md", "# New Title\n\nrewritten body content\n")
    _wait_for(
        lambda: index_db.list_reports(page=1, per_page=10)["items"][0]["title"] == "New Title",
        8,
    )


def test_watcher_removes_deleted_report(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import watcher

    output = str(watcher_env.output)
    path = _write(output, "research/gone.md")
    watcher_env.start(watcher, poll_delay_ms=100)
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 1, 8)

    os.remove(path)
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 0, 8)


def test_watcher_syncs_vectors_for_new_report(watcher_env, monkeypatch):
    from web.indexer import vector_sync
    from web.indexer import watcher

    output = str(watcher_env.output)
    synced: list[str] = []
    monkeypatch.setattr(
        vector_sync, "sync_report",
        lambda output_dir, rel, content=None: synced.append(rel) or {"embedded": 1},
    )
    watcher_env.start(watcher, poll_delay_ms=100)
    time.sleep(0.8)
    _write(output, "research/v.md")
    _wait_for(lambda: bool(synced), 8)
    assert "research/v.md" in synced


def test_watcher_survives_indexing_error(watcher_env, monkeypatch):
    """A failure on one file must not kill the watcher thread."""
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import watcher

    output = str(watcher_env.output)
    real_index = index_scanner.index_file
    calls = {"n": 0}

    def flaky(out_dir, rel):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_index(out_dir, rel)

    monkeypatch.setattr(index_scanner, "index_file", flaky)
    watcher_env.start(watcher, poll_delay_ms=100)
    time.sleep(0.8)
    _write(output, "research/a.md")
    time.sleep(0.5)
    _write(output, "research/b.md")
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 1, 8)


def _wait_for(predicate, timeout: float, interval: float = 0.2) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")
