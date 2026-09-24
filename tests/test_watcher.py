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

from web.indexer import watcher
from conftest import write_report as _write

watchfiles = pytest.importorskip("watchfiles")


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


def test_watcher_writes_a_heartbeat(watcher_env):
    """A dead watcher must leave evidence, not just a log line nobody reads."""
    state = str(watcher_env.state)
    output = str(watcher_env.output)
    thread = watcher_env.start(watcher, reconcile_seconds=1)
    try:
        deadline = time.time() + 5
        beat = None
        while time.time() < deadline:
            beat = watcher.read_heartbeat(str(watcher_env.state))
            if beat:
                break
            time.sleep(0.1)
        assert beat, "no heartbeat written"
        assert beat["stopped"] is False
        assert beat["output_dir"] == str(output)
        assert beat["indexed"] == 0
        assert beat["started_at"]
    finally:
        watcher_env.stop()


def test_heartbeat_records_indexed_updates(watcher_env):
    state = str(watcher_env.state)
    output = str(watcher_env.output)
    watcher_env.start(watcher, reconcile_seconds=1, poll_delay_ms=100)
    try:
        _write(output, "research/heartbeat_probe.md", "# probe\n\nbody about probe\n")
        deadline = time.time() + 8
        indexed = 0
        while time.time() < deadline:
            beat = watcher.read_heartbeat(str(watcher_env.state)) or {}
            indexed = beat.get("indexed", 0)
            if indexed:
                assert beat["last_event_at"]
                break
            time.sleep(0.1)
        assert indexed >= 1, "heartbeat did not record the incremental update"
    finally:
        watcher_env.stop()


def test_reconcile_runs_without_file_events(watcher_env, monkeypatch):
    """The self-heal must not depend on activity.

    Deleting a report produces exactly one delete event; if the periodic
    reconcile only ran when some later event arrived, the orphan row would
    survive for as long as the output directory stayed quiet.
    """
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    _write(watcher_env.output, "quiet.md", "# quiet\n")

    calls = []
    monkeypatch.setattr(index_db, "prune_missing", lambda d: calls.append(d) or 0)
    monkeypatch.setattr(vector_sync, "prune_orphans", lambda d: None)

    watcher_env.start(watcher, reconcile_seconds=1)
    try:
        deadline = time.time() + 12
        while time.time() < deadline and len(calls) < 2:
            time.sleep(0.2)
    finally:
        watcher_env.stop()

    assert len(calls) >= 2, (
        "reconcile did not run on a timer in a quiet directory "
        f"(calls={len(calls)})"
    )
    beat = watcher.read_heartbeat(str(watcher_env.state)) or {}
    assert beat.get("reconciles", 0) >= 1
    assert beat.get("last_reconcile_at")


def test_reconcile_prunes_orphan_fts_rows(watcher_env):
    """Deletes that bypass remove_report leave search hits with no report."""
    from web.indexer import db as index_db

    _write(watcher_env.output, "research/kept.md", "# kept\n\nbody\n")
    index_db.upsert_report("research/kept.md", mtime=1.0, title="kept",
                           content="body")
    with index_db.connect() as conn:
        conn.execute("INSERT INTO reports_fts (rowid, title, content)"
                     " VALUES (4242, 'ghost', 'phantom body')")

    watcher_env.start(watcher, reconcile_seconds=1)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            with index_db.connect() as conn:
                left = conn.execute(
                    "SELECT COUNT(*) FROM reports_fts WHERE rowid NOT IN"
                    " (SELECT rowid FROM reports)").fetchone()[0]
            if not left:
                break
            time.sleep(0.2)
    finally:
        watcher_env.stop()

    assert index_db.prune_fts_orphans() == 0
    with index_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM reports_fts").fetchone()[0] == 1
