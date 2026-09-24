"""Incremental file watcher for output/ directory using watchfiles.

Runs in a background thread and keeps the FTS5 index and the vector store in
step with files on disk.

Note: ``watchfiles.Change`` is an enum whose ``.value`` is an **int**. An
earlier version compared ``change_type.value`` to the strings ``"added"`` /
``"modified"``, which never matched and silently disabled all incremental
indexing; the enum members are compared directly here.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger("ai_research.web.indexer")

MARKDOWN_EXT = (".md", ".mdx")


def _handle_change(change_type, change_path: str, output_dir: str) -> bool:
    """Apply one filesystem change. Returns True when something was indexed.

    Failures are logged and swallowed: one bad file must never kill the
    watcher thread.
    """
    from web.indexer import db as index_db
    from web.indexer import scanner as index_scanner
    from web.indexer import vector_sync
    from watchfiles import Change

    rel = os.path.relpath(change_path, output_dir)
    if rel.startswith(".."):
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


def start_watcher(
    output_dir: str,
    on_change: Optional[Callable[[], None]] = None,
    poll_delay_ms: int = 2000,
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """Start a background thread watching output_dir for file changes.

    Uses watchfiles to detect create/modify/delete events for .md/.mdx files.

    Args:
        output_dir: Path to the output directory.
        on_change: Optional callback invoked after each batch of changes.
        poll_delay_ms: Filesystem poll delay handed to watchfiles.
        stop_event: Set this to stop the watcher (used by tests and shutdown).

    Returns:
        The background thread (daemon=True).
    """
    try:
        from watchfiles import watch
    except ImportError:
        logger.warning("watchfiles not installed; incremental indexing disabled")
        return threading.Thread(target=lambda: None, daemon=True)

    def _watch():
        logger.info("Starting file watcher for: %s", output_dir)
        try:
            for changes in watch(
                output_dir,
                watch_filter=None,
                recursive=True,
                poll_delay_ms=poll_delay_ms,
                stop_event=stop_event,
            ):
                indexed_any = False
                for change_type, change_path in changes:
                    try:
                        if _handle_change(change_type, change_path, output_dir):
                            indexed_any = True
                    except Exception as exc:  # noqa: BLE001 - keep watching
                        logger.warning("Watcher skipped %s: %s", change_path, exc)
                if indexed_any and on_change:
                    try:
                        on_change()
                    except Exception:  # noqa: BLE001 - callback must not kill us
                        logger.warning("on_change callback failed", exc_info=True)
        except Exception as exc:  # noqa: BLE001 - surface watcher death
            logger.error("File watcher stopped: %s", exc)

    thread = threading.Thread(target=_watch, daemon=True, name="report-watcher")
    thread.start()
    return thread
