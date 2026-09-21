"""Incremental file watcher for output/ directory using watchfiles.

Runs in a background thread and updates the FTS5 index on file changes.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger("ai_research.web.indexer")


def start_watcher(
    output_dir: str,
    on_change: Optional[Callable[[], None]] = None,
) -> threading.Thread:
    """Start a background thread watching output_dir for file changes.

    Uses watchfiles to detect create/modify/delete events for .md/.mdx files.

    Args:
        output_dir: Path to the output directory.
        on_change: Optional callback invoked after each batch of changes.

    Returns:
        The background thread (daemon=True).
    """
    from web.indexer import scanner as index_scanner

    def _watch():
        try:
            from watchfiles import watch
        except ImportError:
            logger.warning("watchfiles not installed; incremental indexing disabled")
            return

        logger.info("Starting file watcher for: %s", output_dir)
        try:
            for changes in watch(
                output_dir,
                watch_filter=None,
                recursive=True,
                poll_delay_ms=2000,
            ):
                indexed_any = False
                for change_type, change_path_str in changes:
                    rel = os.path.relpath(change_path_str, output_dir)
                    if change_type.value in ("modified", "added"):
                        if index_scanner.index_file(output_dir, rel):
                            indexed_any = True
                    elif change_type.value == "deleted":
                        try:
                            from web.indexer import db as index_db
                            index_db.remove_report(rel)
                            indexed_any = True
                        except Exception as exc:
                            logger.warning("Failed to remove deleted report %s: %s", rel, exc)

                if indexed_any and on_change:
                    try:
                        on_change()
                    except Exception:
                        logger.warning("on_change callback failed", exc_info=True)
        except Exception as e:
            logger.error("File watcher stopped: %s", e)

    thread = threading.Thread(target=_watch, daemon=True, name="report-watcher")
    thread.start()
    return thread
