"""Keeps the vector store in step with the report index.

The file watcher only maintained FTS5, so freshly generated reports were
full-text searchable but invisible to semantic search until someone pressed
"rebuild vectors". This module embeds new/changed reports as they appear,
removes vectors for deleted reports, and can purge the whole store.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client = None
_unavailable_reason: Optional[str] = None


def _embedding_client():
    """Return a cached EmbeddingClient, or None when embeddings are off."""
    global _client, _unavailable_reason
    if _client is not None or _unavailable_reason is not None:
        return _client
    try:
        from core.config import load_system_config
        from core.embeddings import EmbeddingClient
        from web.settings import get_settings

        settings = get_settings()
        client = EmbeddingClient.from_system(
            load_system_config(settings.paths.config_dir))
        if not getattr(client, "model", ""):
            _unavailable_reason = "ai.embedding_model not configured"
            return None
        _client = client
        return _client
    except Exception as exc:  # noqa: BLE001 - embeddings are optional
        _unavailable_reason = f"{type(exc).__name__}: {exc}"
        logger.info("Vector indexing inactive (%s)", _unavailable_reason)
        return None


def reset_client() -> None:
    """Forget the cached client (after a config change or in tests)."""
    global _client, _unavailable_reason
    with _lock:
        _client = None
        _unavailable_reason = None


def sync_report(output_dir: str, rel_path: str, content: Optional[str] = None) -> dict:
    """Embed one report if needed. Returns a small status dict."""
    from web.indexer import vectors

    result = {"path": rel_path, "embedded": 0, "skipped": False, "reason": ""}
    client = _embedding_client()
    if client is None:
        result["skipped"] = True
        result["reason"] = _unavailable_reason or "embeddings unavailable"
        return result

    full = os.path.join(output_dir, rel_path)
    try:
        if content is None:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        if not vectors.needs_embedding(rel_path, content, client.model):
            result["skipped"] = True
            return result
        with _lock:
            result["embedded"] = vectors.index_report(
                rel_path, content, client.embed, model=client.model)
        logger.info("Vector-indexed %s (%d chunks)", rel_path, result["embedded"])
    except Exception as exc:  # noqa: BLE001 - indexing must not break the watcher
        result["reason"] = f"{type(exc).__name__}: {exc}"
        logger.warning("Vector indexing failed for %s: %s", rel_path, exc)
    return result


def remove_report(rel_path: str) -> dict:
    """Drop vectors and metadata for a report that no longer exists."""
    from web.indexer import vectors

    try:
        vectors.init_vectors()
        from web.indexer import db as index_db

        with index_db.connect() as conn:
            chunks = conn.execute(
                "SELECT COUNT(*) AS c FROM report_chunks WHERE path = ?", (rel_path,)
            ).fetchone()["c"]
            conn.execute("DELETE FROM report_chunks WHERE path = ?", (rel_path,))
            conn.execute("DELETE FROM chunk_meta WHERE path = ?", (rel_path,))
        return {"path": rel_path, "removed_chunks": chunks}
    except Exception as exc:  # noqa: BLE001 - cleanup is best-effort
        logger.warning("Vector cleanup failed for %s: %s", rel_path, exc)
        return {"path": rel_path, "removed_chunks": 0, "error": str(exc)}


def purge_all() -> dict:
    """Clear every vector and metadata row (used before a full rebuild)."""
    from web.indexer import vectors

    vectors.init_vectors()
    from web.indexer import db as index_db

    with index_db.connect() as conn:
        chunks = conn.execute("SELECT COUNT(*) AS c FROM report_chunks").fetchone()["c"]
        conn.execute("DELETE FROM report_chunks")
        conn.execute("DELETE FROM chunk_meta")
    logger.warning("Purged %d vector chunks", chunks)
    return {"removed_chunks": chunks}


def prune_orphans(output_dir: str = "output", state_dir: str = "state") -> dict:
    """Delete vector rows whose report no longer exists on disk or in the index.

    The watcher already cleans up on delete events; this catches leftovers from
    crashes, manual file operations and older versions.
    """
    from web.indexer import db as index_db
    from web.indexer import vectors

    vectors.init_vectors()
    removed_meta = 0
    removed_chunks = 0
    with index_db.connect() as conn:
        paths = [r["path"] for r in conn.execute("SELECT path FROM chunk_meta")]
        indexed = {r["path"] for r in conn.execute("SELECT path FROM reports")}
        for path in paths:
            if os.path.isfile(os.path.join(output_dir, path)) and path in indexed:
                continue
            cur = conn.execute("DELETE FROM report_chunks WHERE path = ?", (path,))
            removed_chunks += cur.rowcount or 0
            conn.execute("DELETE FROM chunk_meta WHERE path = ?", (path,))
            removed_meta += 1
    if removed_meta:
        logger.warning("Pruned %d orphaned vector documents (%d chunks)",
                       removed_meta, removed_chunks)
    return {"orphan_documents": removed_meta, "orphan_chunks": removed_chunks}


def coverage(state_dir: str = "state") -> dict:
    """How many indexed reports have embeddings (drives the health check)."""
    import sqlite3

    reports_db = os.path.join(state_dir, "reports.db")
    if not os.path.isfile(reports_db):
        return {"total": 0, "embedded": 0, "missing": 0, "orphan_vectors": 0}
    try:
        conn = sqlite3.connect(f"file:{reports_db}?mode=ro", uri=True, timeout=2)
        try:
            total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            try:
                embedded = conn.execute("SELECT COUNT(*) FROM chunk_meta").fetchone()[0]
                orphan = conn.execute(
                    "SELECT COUNT(*) FROM chunk_meta m WHERE NOT EXISTS"
                    " (SELECT 1 FROM reports r WHERE r.path = m.path)"
                ).fetchone()[0]
            except sqlite3.Error:
                embedded = 0
                orphan = 0
        finally:
            conn.close()
    except sqlite3.Error:
        return {"total": 0, "embedded": 0, "missing": 0, "orphan_vectors": 0}
    return {
        "total": total,
        "embedded": embedded,
        "missing": max(0, total - embedded),
        "orphan_vectors": orphan,
    }
