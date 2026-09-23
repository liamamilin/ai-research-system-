"""Persistent tracking for round action items, tests and watchlist topics.

Items are keyed by a content hash so the same action appearing in a later
round continues the same entry. Because wording drifts between rounds, an
exact hash match is followed by a similarity match so "建立成本日历" and
"把弃用写进日历" stay one item. Stored in ``state/tracking.db``.
"""

from __future__ import annotations

import difflib
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

# Calibrated against real rounds: reworded duplicates score 0.59-0.77,
# related-but-different items 0.30-0.40, unrelated 0.00-0.18, and the best
# cross-round match in 2026-09-19 vs 2026-09-24 was 0.16. 0.55 keeps genuine
# rewrites together without collapsing distinct actions.
DEFAULT_SIMILARITY_THRESHOLD = 0.55
DEDUP_THRESHOLD = float(
    os.environ.get("AI_TRACKING_DEDUP_THRESHOLD", DEFAULT_SIMILARITY_THRESHOLD)
)
CANDIDATE_LIMIT = 2000

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
            CREATE TABLE IF NOT EXISTS outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                decided_at TEXT NOT NULL,
                decided_by TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_outcomes_decided ON outcomes(decided_at);
            """
        )


def _normalize(text: str) -> str:
    text = _NUMBERING_RE.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def _tokens(text: str) -> set[str]:
    """Token set for similarity: ASCII words plus CJK character bigrams."""
    norm = _normalize(text)
    tokens = set(re.findall(r"[a-z0-9]+", norm))
    cjk = re.sub(r"[^一-鿿]", "", norm)
    tokens.update(cjk[i:i + 2] for i in range(len(cjk) - 1))
    if len(cjk) == 1:
        tokens.add(cjk)
    return tokens


def similarity(a: str, b: str) -> float:
    """Blend token overlap with character similarity (0..1)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    seq = difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
    return 0.5 * jaccard + 0.5 * seq


def item_id(kind: str, text: str) -> str:
    digest = hashlib.sha1(f"{kind}:{_normalize(text)}".encode("utf-8")).hexdigest()
    return digest[:16]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _row_text(row: dict, *keys: str) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _find_similar(conn: sqlite3.Connection, kind: str, text: str,
                  threshold: float) -> Optional[tuple[str, str, float]]:
    """Return (id, status, score) of the closest existing item above threshold."""
    rows = conn.execute(
        "SELECT id, text, status FROM items WHERE kind = ?"
        " ORDER BY last_seen DESC LIMIT ?",
        (kind, CANDIDATE_LIMIT),
    ).fetchall()
    best: Optional[tuple[str, str, float]] = None
    for row in rows:
        score = similarity(text, row["text"])
        if score >= threshold and (best is None or score > best[2]):
            best = (row["id"], row["status"], score)
    return best


def _append_note(existing: str, line: str) -> str:
    lines = [l for l in (existing or "").splitlines() if l.strip()]
    if line in lines:
        return "\n".join(lines)
    lines.append(line)
    return "\n".join(lines)


def sync_round(date: str, actions: Optional[list] = None,
               tests: Optional[list] = None,
               watchlist: Optional[list] = None) -> dict:
    """Upsert the items of one round.

    Returns ``{new, updated, merged, dropped, warnings}`` where ``merged``
    counts items folded into an existing entry by similarity.
    """
    entries: list[tuple[str, str, str]] = []
    for row in actions or []:
        text = _row_text(row, "action", "行动", "text")
        if text:
            entries.append(("action", text, _row_text(row, "priority", "优先级")))
    for row in tests or []:
        text = _row_text(row, "test", "测试", "text")
        if text:
            entries.append(("test", text, _row_text(row, "priority", "优先级")))
    for row in watchlist or []:
        text = _row_text(row, "topic", "议题", "观察项", "text")
        if text:
            entries.append(("watch", text, ""))

    init_db()
    new = updated = merged = 0
    warnings: list[str] = []
    now = _now()
    threshold = DEDUP_THRESHOLD
    with _lock, connect() as conn:
        for kind, text, priority in entries:
            iid = item_id(kind, text)
            row = conn.execute("SELECT last_seen, times_seen FROM items WHERE id = ?",
                               (iid,)).fetchone()
            if row is None:
                match = _find_similar(conn, kind, text, threshold)
                if match:
                    iid, status, score = match
                    merged += 1
                    current = conn.execute("SELECT note, last_seen FROM items WHERE id = ?",
                                           (iid,)).fetchone()
                    seen_again = current["last_seen"] != date
                    note_text = _append_note(
                        current["note"] or "",
                        f"[{date}] 相似条目再次出现（相似度 {score:.2f}）：{text[:60]}",
                    )
                    conn.execute(
                        "UPDATE items SET last_seen = ?, times_seen = times_seen + ?,"
                        " note = ?, updated_at = ? WHERE id = ?",
                        (date, 1 if seen_again else 0, note_text, now, iid),
                    )
                    if status != "open":
                        warnings.append(
                            f"{kind}: 相似条目已是 '{status}' 状态，本轮仍被提出：{text[:40]}"
                        )
                    continue
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
    return {"new": new, "updated": updated, "merged": merged,
            "dropped": 0, "warnings": warnings}


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


def set_status(iid: str, status: str, note: Optional[str] = None,
               decided_by: str = "") -> bool:
    """Change an item's status and record the decision for later prompts."""
    if status not in STATUSES:
        raise ValueError(f"invalid status: {status}")
    init_db()
    with _lock, connect() as conn:
        row = conn.execute("SELECT kind, text, status, note FROM items WHERE id = ?",
                           (iid,)).fetchone()
        if row is None:
            return False
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
        if status != row["status"] or (note is not None and note != row["note"]):
            conn.execute(
                "INSERT INTO outcomes (item_id, kind, text, status, note, decided_at, decided_by)"
                " VALUES (?,?,?,?,?,?,?)",
                (iid, row["kind"], row["text"], status, note or row["note"] or "",
                 _now(), decided_by or ""),
            )
        return cur.rowcount > 0


def recent_outcomes(limit: int = 10, days: int = 30) -> list[dict]:
    """Most recent done/dropped decisions, newest first."""
    init_db()
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S%z",
                           time.localtime(time.time() - days * 86400))
    with connect() as conn:
        rows = conn.execute(
            "SELECT item_id, kind, text, status, note, decided_at, decided_by"
            " FROM outcomes WHERE decided_at >= ? ORDER BY decided_at DESC, id DESC LIMIT ?",
            (cutoff, int(limit)),
        ).fetchall()
    return [dict(r) for r in rows]


def format_outcomes(limit: int = 8, days: int = 30) -> str:
    """Render recent outcomes as a prompt block (empty string when none)."""
    try:
        outcomes = recent_outcomes(limit=limit, days=days)
    except sqlite3.Error:
        return ""
    if not outcomes:
        return ""
    lines = ["上一轮行动结果（已由使用者判定，请勿重复建议已完成的事项）："]
    for o in outcomes:
        when = (o.get("decided_at") or "")[:10]
        label = "已完成" if o["status"] == "done" else "已放弃"
        extra = f"（备注：{o['note']}）" if o.get("note") else ""
        lines.append(f"- [{label} {when}] {o['text']}{extra}")
    return "\n".join(lines)


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
