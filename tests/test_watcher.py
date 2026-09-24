"""Incremental report indexing watcher.

The watcher compared ``change_type.value`` to the strings ``"added"`` /
``"modified"``, but ``watchfiles.Change`` is an enum whose ``.value`` is an
**int** — the comparison never matched and incremental indexing had silently
never run.

Thread-level tests cover start/stop; the per-change logic is tested by calling
``_handle_change`` directly so assertions never depend on FSEvents timing.
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


# --- enum contract (the original bug) --------------------------------------


def test_change_enum_values_are_not_strings():
    """Guard the exact bug: comparing .value to strings silently matches nothing."""
    assert watchfiles.Change.added.value != "added"
    assert watchfiles.Change.modified.value != "modified"
    assert watchfiles.Change.deleted.value != "deleted"


# --- per-change logic (no timing involved) ---------------------------------


def test_handle_change_indexes_added_file(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import watcher

    _write(watcher_env.output, "research/new.md")
    assert watcher._handle_change(
        watchfiles.Change.added,
        os.path.join(str(watcher_env.output), "research/new.md"),
        str(watcher_env.output),
    ) is True
    rows = index_db.list_reports(page=1, per_page=10)["items"]
    assert [r["path"] for r in rows] == ["research/new.md"]


def test_handle_change_reindexes_modified_file(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import watcher

    rel = "research/a.md"
    path = _write(watcher_env.output, rel, "# First Title\n\nfirst body\n")
    index_scanner.index_file(str(watcher_env.output), rel)
    assert index_db.list_reports(page=1, per_page=10)["items"][0]["title"] == "First Title"

    _write(watcher_env.output, rel, "# Second Title\n\nsecond body, longer now\n")
    watcher._handle_change(watchfiles.Change.modified, path, str(watcher_env.output))
    assert index_db.list_reports(page=1, per_page=10)["items"][0]["title"] == "Second Title"


def test_handle_change_removes_deleted_report(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import watcher

    rel = "research/gone.md"
    path = _write(watcher_env.output, rel)
    index_scanner.index_file(str(watcher_env.output), rel)
    assert index_db.list_reports(page=1, per_page=10)["total"] == 1

    os.remove(path)
    assert watcher._handle_change(
        watchfiles.Change.deleted, path, str(watcher_env.output)) is True
    assert index_db.list_reports(page=1, per_page=10)["total"] == 0


def test_handle_change_ignores_paths_outside_output(watcher_env):
    from web.indexer import watcher

    outside = os.path.join(str(watcher_env.output), "..", "escape.md")
    assert watcher._handle_change(
        watchfiles.Change.added, outside, str(watcher_env.output)) is False


def test_handle_change_syncs_vectors(watcher_env, monkeypatch):
    from web.indexer import vector_sync
    from web.indexer import watcher

    synced: list[str] = []
    monkeypatch.setattr(
        vector_sync, "sync_report",
        lambda output_dir, rel, content=None: synced.append(rel) or {"embedded": 2},
    )
    _write(watcher_env.output, "research/v.md")
    watcher._handle_change(
        watchfiles.Change.added,
        os.path.join(str(watcher_env.output), "research/v.md"),
        str(watcher_env.output),
    )
    assert synced == ["research/v.md"]


# --- reconciliation (self-heal for missed delete events) -------------------


def test_reconcile_prunes_rows_for_vanished_files(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner

    output = str(watcher_env.output)
    _write(output, "research/kept.md")
    _write(output, "research/vanished.md")
    index_scanner.index_file(output, "research/kept.md")
    with index_db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports (path, size_bytes, mtime, title, indexed_at)"
            " VALUES ('research/vanished.md', 10, 1.0, 'V', 1.0)")
    os.remove(os.path.join(output, "research/vanished.md"))

    assert index_db.prune_missing(output) == 1
    paths = [i["path"] for i in index_db.list_reports(page=1, per_page=10)["items"]]
    assert paths == ["research/kept.md"]


def test_prune_missing_is_noop_when_all_present(watcher_env):
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner

    output = str(watcher_env.output)
    _write(output, "research/one.md")
    index_scanner.index_file(output, "research/one.md")
    assert index_db.prune_missing(output) == 0


# --- thread lifecycle -------------------------------------------------------


def test_watcher_indexes_new_report(watcher_env):
    """End-to-end: a file appearing on disk lands in the index without restart."""
    from web.indexer import db as index_db
    from web.indexer import watcher

    output = str(watcher_env.output)
    calls: list[int] = []
    watcher_env.start(watcher, poll_delay_ms=100, on_change=lambda: calls.append(1))
    time.sleep(1.0)
    _write(output, "research/live.md")
    _wait_for(lambda: index_db.list_reports(page=1, per_page=10)["total"] == 1, 25)
    assert calls, "on_change callback should have fired"


def test_watcher_stops_with_stop_event(watcher_env):
    from web.indexer import watcher

    stop = threading.Event()
    thread = watcher.start_watcher(str(watcher_env.output), stop_event=stop,
                                   poll_delay_ms=100)
    time.sleep(0.5)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_watcher_survives_indexing_error(watcher_env, monkeypatch):
    """A failure on one file must not stop later files from being indexed."""
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import watcher

    output = str(watcher_env.output)
    real_index = index_scanner.index_file
    failed = {"boom": False}

    def flaky(out_dir, rel):
        # Only fail once, and only for a real report file (watchers also emit
        # directory events, which must keep working).
        if rel.endswith(".md") and not failed["boom"]:
            failed["boom"] = True
            raise RuntimeError("boom")
        return real_index(out_dir, rel)

    monkeypatch.setattr(index_scanner, "index_file", flaky)
    watcher_env.start(watcher, poll_delay_ms=100, reconcile_seconds=0)
    time.sleep(1.0)

    _write(output, "research/a.md")
    time.sleep(1.0)
    _write(output, "research/b.md")

    def b_indexed() -> bool:
        paths = [i["path"] for i in index_db.list_reports(page=1, per_page=10)["items"]]
        return "research/b.md" in paths

    _wait_for(b_indexed, 25)
    assert failed["boom"] is True, "the failing index call should have happened"


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
