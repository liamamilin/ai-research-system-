"""Incremental file watcher for output/ directory using watchfiles.

Runs in a background thread and keeps the FTS5 index and the vector store in
step with files on disk.

Note: ``watchfiles.Change`` is an enum whose ``.value`` is an **int**. An
earlier version compared ``change_type.value`` to the strings ``"added"`` /
``"modified"``, which never matched and silently disabled all incremental
indexing; the enum members are compared directly here.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
import time
from typing import Callable, Optional

logger = logging.getLogger("ai_research.web.indexer")

MARKDOWN_EXT = (".md", ".mdx")


def _relative_path(change_path: str, output_dir: str) -> Optional[str]:
    """Path of a change relative to the watched root, or None when outside.

    Both sides are resolved with ``realpath``: watchers report canonical paths
    (e.g. macOS ``/private/var/...`` for ``/var/...``), so a plain relpath would
    escape the root and every event would be discarded.
    """
    try:
        root = os.path.realpath(output_dir)
        target = os.path.realpath(change_path)
        rel = os.path.relpath(target, root)
    except (OSError, ValueError):
        return None
    if rel == "." or rel.startswith(".."):
        return None
    return rel


def _handle_change(change_type, change_path: str, output_dir: str) -> bool:
    """Apply one filesystem change. Returns True when something was indexed.

    Failures are logged and swallowed: one bad file must never kill the
    watcher thread.
    """
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import vector_sync
    from watchfiles import Change

    rel = _relative_path(change_path, output_dir)
    if rel is None:
        return False

    if change_type in (Change.added, Change.modified):
        indexed = index_scanner.index_file(output_dir, rel)
        if os.path.splitext(rel)[1].lower() in MARKDOWN_EXT:
            result = vector_sync.sync_report(output_dir, rel)
            if result.get("embedded"):
                indexed = True
            elif result.get("reason") and not result.get("skipped"):
                logger.warning("Vector sync failed for %s: %s", rel, result["reason"])
        return indexed

    if change_type == Change.deleted:
        index_db.remove_report(rel)
        vector_sync.remove_report(rel)
        return True

    return False


HEARTBEAT_NAME = "watcher.json"


def heartbeat_path(state_dir: str = "state") -> str:
    return os.path.join(state_dir, HEARTBEAT_NAME)


def _write_heartbeat(state_dir: str, **fields) -> None:
    """Persist what the watcher is doing, so health checks can see it.

    A watcher that dies leaves no trace except a log line nobody reads: the
    index then silently stops updating while every count-based check still
    looks fine.
    """
    try:
        from core.fileio import atomic_write

        payload = dict(fields)
        payload["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        atomic_write(heartbeat_path(state_dir),
                     json.dumps(payload, ensure_ascii=False, indent=1))
    except Exception as exc:  # noqa: BLE001 - telemetry must never break watching
        logger.debug("watcher heartbeat not written: %s", exc)


def read_heartbeat(state_dir: str = "state") -> Optional[dict]:
    """The watcher's last self-report, or None when it never ran."""
    try:
        with open(heartbeat_path(state_dir), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def start_watcher(
    output_dir: str,
    on_change: Optional[Callable[[], None]] = None,
    poll_delay_ms: int = 2000,
    stop_event: Optional[threading.Event] = None,
    reconcile_seconds: int = 300,
    state_dir: str = "state",
) -> threading.Thread:
    """Start a background thread watching output_dir for file changes.

    Uses watchfiles to detect create/modify/delete events for .md/.mdx files.

    Args:
        output_dir: Path to the output directory.
        on_change: Optional callback invoked after each batch of changes.
        poll_delay_ms: Filesystem poll delay handed to watchfiles.
        stop_event: Set this to stop the watcher (used by tests and shutdown).
        reconcile_seconds: How often to drop index rows whose file vanished.

    Returns:
        The background thread (daemon=True).
    """
    try:
        from watchfiles import watch
    except ImportError:
        logger.warning("watchfiles not installed; incremental indexing disabled")
        _write_heartbeat(state_dir, stopped=True, output_dir=output_dir,
                         error="watchfiles not installed",
                         started_at=datetime.now().astimezone().isoformat(timespec="seconds"))
        return threading.Thread(target=lambda: None, daemon=True)

    def _reconcile() -> None:
        """Self-heal missed delete events."""
        from web.indexer import db as index_db
        from web.indexer import vector_sync

        try:
            removed = index_db.prune_missing(output_dir)
            fts_orphans = index_db.prune_fts_orphans()
            if removed:
                logger.info("Reconciled %d stale report index row(s)", removed)
            if fts_orphans:
                logger.info("Reconciled %d orphan full-text row(s)", fts_orphans)
            if removed or fts_orphans:
                vector_sync.prune_orphans(output_dir)
        except Exception as exc:  # noqa: BLE001 - never kill the watcher
            logger.warning("Index reconciliation failed: %s", exc)

    def _watch():
        logger.info("Starting file watcher for: %s", output_dir)
        started_at = datetime.now().astimezone().isoformat(timespec="seconds")
        next_reconcile = time.time() + reconcile_seconds
        stats = {"indexed": 0, "errors": 0, "reconciles": 0,
                 "last_event_at": "", "last_reconcile_at": ""}

        def beat(**extra) -> None:
            # `extra` may carry stopped/error, so it must win over the defaults.
            payload = {"stopped": False}
            payload.update(extra)
            _write_heartbeat(state_dir, output_dir=output_dir,
                             started_at=started_at, **stats, **payload)

        beat()
        try:
            # yield_on_timeout wakes the loop on a timer even when nothing
            # changes. Without it the periodic reconcile below only runs after
            # a file event — and a deleted report produces no further events,
            # which is exactly when the self-heal matters most.
            for changes in watch(
                output_dir,
                watch_filter=None,
                recursive=True,
                poll_delay_ms=poll_delay_ms,
                stop_event=stop_event,
                yield_on_timeout=True,
                rust_timeout=1000,
            ):
                indexed_any = False
                for change_type, change_path in changes:
                    try:
                        if _handle_change(change_type, change_path, output_dir):
                            indexed_any = True
                    except Exception as exc:  # noqa: BLE001 - keep watching
                        stats["errors"] += 1
                        logger.warning("Watcher skipped %s: %s", change_path, exc)
                if indexed_any:
                    stats["indexed"] += 1
                    stats["last_event_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    beat()
                if indexed_any and on_change:
                    try:
                        on_change()
                    except Exception:  # noqa: BLE001 - callback must not kill us
                        logger.warning("on_change callback failed", exc_info=True)

                if time.time() >= next_reconcile:
                    next_reconcile = time.time() + reconcile_seconds
                    _reconcile()
                    stats["reconciles"] += 1
                    stats["last_reconcile_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
                    beat()
        except Exception as exc:  # noqa: BLE001 - surface watcher death
            logger.error("File watcher stopped: %s", exc)
            beat(stopped=True, error=f"{type(exc).__name__}: {exc}")
        else:
            beat(stopped=True, error=None)

    thread = threading.Thread(target=_watch, daemon=True, name="report-watcher")
    thread.start()
    return thread
