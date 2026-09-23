"""Persistent tracking for round action items, tests and watchlist topics.

Items are keyed by a content hash so the same action appearing in a later
round continues the same entry. Stored in ``state/tracking.db``.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

DB_FILE = os.path.join("state", "tracking.db")

KINDS = ("action", "test", "watch")
STATUSES = ("open", "done", "dropped")

_DB_PATH_OVERRIDE: Optional[str] = None
_lock = threading.Lock()

_NUMBERING_RE = re.compile(r"^\s*\d+\s*[.、)]\s*")


def db_path() -> str:
    return _DB_PATH_OVERRIDE or DB_FILE


def set_db_path(path: Optional[str]) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


def use_state_dir(state_dir: str) -> None:
    """Point the store at ``<state_dir>/tracking.db``."""
    set_db_path(os.path.join(state_dir, "tracking.db"))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                times_seen INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'open',
                note TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind, status);
            CREATE INDEX IF NOT EXISTS idx_items_last_seen ON items(last_seen);
            """
        )


def _normalize(text: str) -> str:
    text = _NUMBERING_RE.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def item_id(kind: str, text: str) -> str:
    digest = hashlib.sha1(f"{kind}:{_normalize(text)}".encode("utf-8")).hexdigest()
    return digest[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sync_round(date: str, actions: Optional[list] = None,
               tests: Optional[list] = None,
               watchlist: Optional[list] = None) -> dict:
    """Upsert the items of one round. Returns {new, updated} counts."""
    entries: list[tuple[str, str, str]] = []
    for row in actions or []:
        text = (row.get("action") or "").strip()
        if text:
            entries.append(("action", text, (row.get("priority") or "").strip()))
    for row in tests or []:
        text = (row.get("test") or "").strip()
        if text:
            entries.append(("test", text, (row.get("priority") or "").strip()))
    for row in watchlist or []:
        text = (row.get("topic") or "").strip()
        if text:
            entries.append(("watch", text, ""))

    init_db()
    new = updated = 0
    now = _now()
    with _lock, connect() as conn:
        for kind, text, priority in entries:
            iid = item_id(kind, text)
            row = conn.execute("SELECT last_seen, times_seen FROM items WHERE id = ?",
                               (iid,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO items (id, kind, text, priority, first_seen, last_seen,"
                    " times_seen, status, note, updated_at)"
                    " VALUES (?,?,?,?,?,?,1,'open','',?)",
                    (iid, kind, text, priority, date, date, now),
                )
                new += 1
            else:
                seen_again = row["last_seen"] != date
                conn.execute(
                    "UPDATE items SET last_seen = ?, times_seen = times_seen + ?,"
                    " priority = CASE WHEN ? != '' THEN ? ELSE priority END,"
                    " updated_at = ? WHERE id = ?",
                    (date, 1 if seen_again else 0, priority, priority, now, iid),
                )
                updated += 1
    return {"new": new, "updated": updated}


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "priority": row["priority"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "times_seen": row["times_seen"],
        "status": row["status"],
        "note": row["note"],
        "updated_at": row["updated_at"],
    }


def list_items(kind: Optional[str] = None, status: Optional[str] = None,
               limit: int = 500) -> list[dict]:
    init_db()
    query = "SELECT * FROM items"
    params: list = []
    clauses = []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += (" ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'done' THEN 1 ELSE 2 END,"
              " last_seen DESC, priority ASC LIMIT ?")
    params.append(int(limit))
    with connect() as conn:
        return [_row_to_dict(r) for r in conn.execute(query, params).fetchall()]


def get_item(iid: str) -> Optional[dict]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM items WHERE id = ?", (iid,)).fetchone()
    return _row_to_dict(row) if row else None


def set_status(iid: str, status: str, note: Optional[str] = None) -> bool:
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    init_db()
    with _lock, connect() as conn:
        if note is None:
            cur = conn.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), iid),
            )
        else:
            cur = conn.execute(
                "UPDATE items SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                (status, note, _now(), iid),
            )
        return cur.rowcount > 0


def latest_round_date() -> Optional[str]:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT MAX(last_seen) AS d FROM items").fetchone()
    return row["d"] if row and row["d"] else None


def carry_over(date: Optional[str] = None) -> dict:
    """Classify tracked items relative to the latest (or given) round date."""
    init_db()
    date = date or latest_round_date()
    if not date:
        return {"date": None, "new": [], "continuing": [], "open_stale": []}

    with connect() as conn:
        rows = [_row_to_dict(r) for r in conn.execute("SELECT * FROM items").fetchall()]

    new = [r for r in rows if r["last_seen"] == date and r["first_seen"] == date]
    continuing = [r for r in rows if r["last_seen"] == date and r["first_seen"] != date]
    open_stale = [r for r in rows if r["status"] == "open" and r["last_seen"] != date]

    def order(items: list[dict]) -> list[dict]:
        return sorted(items, key=lambda r: (r["kind"], r["priority"], r["text"]))

    return {
        "date": date,
        "new": order(new),
        "continuing": order(continuing),
        "open_stale": order(open_stale),
    }
