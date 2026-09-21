"""SQLite + FTS5 schema for the reports index."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from typing import Iterator

_DB_PATH_OVERRIDE: str | None = None
_lock = threading.Lock()


def db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    from web.settings import get_settings
    return os.path.join(get_settings().paths.state_dir, "reports.db")


def set_db_path(path: str):
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables and FTS5 virtual table if they don't exist."""
    with _lock, connect() as conn:
        conn.executescript(
            """
    CREATE TABLE IF NOT EXISTS reports (
        path TEXT PRIMARY KEY,
        job_name TEXT DEFAULT '',
        size_bytes INTEGER DEFAULT 0,
        mtime REAL NOT NULL,
        title TEXT DEFAULT '',
        category TEXT DEFAULT '',
        indexed_at REAL NOT NULL
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
        title, content,
        tokenize='unicode61'
    );

    CREATE INDEX IF NOT EXISTS idx_reports_mtime ON reports(mtime);
    CREATE INDEX IF NOT EXISTS idx_reports_category ON reports(category);
    """
        )


# ----- CRUD -----


def upsert_report(
    path: str,
    mtime: float,
    size_bytes: int = 0,
    job_name: str = "",
    title: str = "",
    category: str = "",
    content: str = "",
):
    """Insert or update a report entry."""
    import time
    now = time.time()
    with connect() as conn:
        # Get old rowid if exists (to update FTS)
        old = conn.execute("SELECT rowid FROM reports WHERE path = ?", (path,)).fetchone()
        old_rowid = old["rowid"] if old else None

        conn.execute(
            """
            INSERT INTO reports (path, job_name, size_bytes, mtime, title, category, indexed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime=excluded.mtime,
                size_bytes=excluded.size_bytes,
                job_name=excluded.job_name,
                title=excluded.title,
                category=excluded.category,
                indexed_at=excluded.indexed_at
            """,
            (path, job_name, size_bytes, mtime, title, category, now),
        )

        # Get (or re-get) rowid for FTS sync
        row = conn.execute("SELECT rowid FROM reports WHERE path = ?", (path,)).fetchone()
        if row:
            rowid = row["rowid"]
            if old_rowid and old_rowid != rowid:
                conn.execute("DELETE FROM reports_fts WHERE rowid = ?", (old_rowid,))
            conn.execute("DELETE FROM reports_fts WHERE rowid = ?", (rowid,))
            conn.execute(
                "INSERT INTO reports_fts (rowid, title, content) VALUES (?, ?, ?)",
                (rowid, title, content or ""),
            )


def remove_report(path: str):
    """Delete a report entry."""
    with connect() as conn:
        row = conn.execute("SELECT rowid FROM reports WHERE path = ?", (path,)).fetchone()
        if row:
            conn.execute("DELETE FROM reports_fts WHERE rowid = ?", (row["rowid"],))
        conn.execute("DELETE FROM reports WHERE path = ?", (path,))


def search_reports(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across report titles and content using FTS5.

    Returns results with highlighted snippets.
    """
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                r.path,
                r.job_name,
                r.title,
                r.category,
                r.size_bytes,
                r.mtime,
                snippet(reports_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet
            FROM reports_fts
            JOIN reports r ON r.rowid = reports_fts.rowid
            WHERE reports_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def list_reports(
    page: int = 1,
    per_page: int = 20,
    category: str | None = None,
    job_name: str | None = None,
) -> dict:
    """Paginated list of reports with optional filters."""
    where_parts: list[str] = []
    params: list = []

    if category:
        where_parts.append("category = ?")
        params.append(category)
    if job_name:
        where_parts.append("job_name = ?")
        params.append(job_name)

    where = ""
    if where_parts:
        where = " WHERE " + " AND ".join(where_parts)

    with connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM reports{where}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM reports{where} ORDER BY mtime DESC LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total > 0 else 1,
    }


def list_categories() -> list[str]:
    """Return distinct category names."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM reports WHERE category != '' ORDER BY category"
        ).fetchall()
        return [r["category"] for r in rows]


def get_report_tree() -> list[dict]:
    """Return a nested directory tree structure of output/. Each leaf has path + title."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT path, title, category, size_bytes, mtime FROM reports ORDER BY path"
        ).fetchall()

    tree: dict[str, dict] = {}
    for r in rows:
        parts = r["path"].split("/")
        node = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node[part] = {
                    "type": "file",
                    "path": r["path"],
                    "title": r["title"],
                    "size": r["size_bytes"],
                    "mtime": r["mtime"],
                }
            else:
                if part not in node:
                    node[part] = {"type": "dir", "children": {}}
                node = node[part]["children"]

    def _to_list(d: dict) -> list[dict]:
        result = []
        for key, val in sorted(d.items(), key=lambda x: (0 if x[1].get("type") == "dir" else 1, x[0])):
            if val["type"] == "dir":
                children = _to_list(val["children"])
                entry: dict = {"name": key, "type": "dir", "children": children}
                if children:
                    latest = max(children, key=lambda c: c.get("mtime", 0) if c.get("type") == "file" else 0)
                    if latest.get("mtime"):
                        entry["mtime"] = latest["mtime"]
                result.append(entry)
            else:
                result.append(val)
        return result

    return _to_list(tree)


def get_stats() -> dict:
    """Return summary statistics about indexed reports."""
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS cnt FROM reports").fetchone()["cnt"]
        total_size = conn.execute("SELECT COALESCE(SUM(size_bytes), 0) AS s FROM reports").fetchone()["s"]
        cats = conn.execute("SELECT COUNT(DISTINCT category) AS c FROM reports").fetchone()["c"]
    return {"total": total, "total_size_bytes": total_size, "categories": cats}
