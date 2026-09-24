"""Cross-round event memory: what has already been reported.

Each finished report registers the sources it cited. The next round can then
be told which links were already covered, so the same story stops reappearing
in "new findings" every morning. Fingerprints are normalized URLs (reports
cite bare URLs in tables, not markdown links), with a short context snippet for
human/model recognition.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

from core.provenance import extract_urls, normalize_url

DB_FILE = os.path.join("state", "events.db")

CONTEXT_CHARS = 140
MAX_ROUNDS_KEPT = 8
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>()\[\]\"'，。；：、）】《》]+")
_MARKDOWN_NOISE_RE = re.compile(r"[|*_#>`~\[\]]+")

# Cell labels that carry no information ("Source", "Evidence", "来源"…).
GENERIC_LABELS = {
    "来源", "来源：", "资料来源", "证据来源", "参考", "参考文献", "链接", "网址",
    "出处", "依据", "备注", "证据", "source", "sources", "reference",
    "references", "evidence", "link", "url", "see also",
}

_DB_PATH_OVERRIDE: Optional[str] = None
_lock = threading.RLock()


def db_path() -> str:
    return _DB_PATH_OVERRIDE or DB_FILE


def set_db_path(path: Optional[str]) -> None:
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


def use_state_dir(state_dir: str) -> None:
    set_db_path(os.path.join(state_dir, "events.db"))


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
            CREATE TABLE IF NOT EXISTS events (
                url_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                host TEXT NOT NULL DEFAULT '',
                context TEXT NOT NULL DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                times_seen INTEGER NOT NULL DEFAULT 1,
                jobs TEXT NOT NULL DEFAULT '',
                rounds TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_events_last_seen ON events(last_seen);
            CREATE INDEX IF NOT EXISTS idx_events_host ON events(host);
            """
        )


def _host(url_key: str) -> str:
    return url_key.split("/", 3)[2] if url_key.count("/") >= 2 else url_key


def _context_for(text: str, url: str) -> str:
    """A short label describing the citation.

    Reports cite bare URLs inside table cells, so the surrounding paragraph is
    useless. Prefer the text of the cell that holds the URL, then the row's
    leading label (usually the finding's name).
    """
    line = ""
    for candidate in (text or "").splitlines():
        if url in candidate:
            line = candidate
            break
    if not line:
        return ""

    cells = [c.strip() for c in line.split("|")]
    cell = next((c for c in cells if url in c), "")
    label = re.sub(_URL_IN_TEXT_RE, " ", cell)
    label = _MARKDOWN_NOISE_RE.sub(" ", label)
    label = re.sub(r"\s+", " ", label).strip(" -–—:：;；")

    if label.strip(" :：-").lower() in GENERIC_LABELS or len(label) < 4:
        label = ""
        for c in cells:
            candidate = re.sub(_URL_IN_TEXT_RE, " ", c)
            candidate = _MARKDOWN_NOISE_RE.sub(" ", candidate)
            candidate = re.sub(r"\s+", " ", candidate).strip(" -–—:：;；")
            if len(candidate) >= 4 and candidate.strip(" :：-").lower() not in GENERIC_LABELS:
                label = candidate
                break
    return label[-CONTEXT_CHARS:]


def _append(value: str, item: str, cap: int = MAX_ROUNDS_KEPT) -> str:
    items = [v for v in (value or "").split(",") if v]
    if item and item not in items:
        items.append(item)
    return ",".join(items[-cap:])


def register_report(job: str, date: str, content: str,
                    round_date: Optional[str] = None) -> dict:
    """Register every URL a report cited. Returns per-round counts."""
    init_db()
    urls = extract_urls(content or "")
    if not urls:
        return {"new": 0, "repeat": 0, "total": 0}

    new = repeat = 0
    with _lock, connect() as conn:
        for url in urls:
            key = normalize_url(url)
            if not key:
                continue
            row = conn.execute("SELECT * FROM events WHERE url_key = ?", (key,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO events (url_key, url, host, context, first_seen,"
                    " last_seen, times_seen, jobs, rounds)"
                    " VALUES (?,?,?,?,?,?,1,?,?)",
                    (key, url, _host(key), _context_for(content, url), date, date,
                     job, round_date or ""),
                )
                new += 1
                continue
            already_this_round = bool(round_date) and round_date in (row["rounds"] or "").split(",")
            conn.execute(
                "UPDATE events SET last_seen = ?, times_seen = times_seen + ?,"
                " context = CASE WHEN ? = '' THEN context ELSE ? END,"
                " jobs = ?, rounds = ? WHERE url_key = ?",
                (date, 0 if already_this_round else 1,
                 _context_for(content, url), _context_for(content, url),
                 _append(row["jobs"], job, 5), _append(row["rounds"], round_date or ""), key),
            )
            if not already_this_round:
                repeat += 1
    return {"new": new, "repeat": repeat, "total": len(urls)}


def recent_events(days: int = 7, limit: int = 50,
                  before: Optional[str] = None) -> list[dict]:
    """Events seen within the last ``days`` days, most recent first."""
    init_db()
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    query = "SELECT * FROM events WHERE last_seen >= ?"
    params: list = [cutoff]
    if before:
        query += " AND first_seen < ?"
        params.append(before)
    query += " ORDER BY last_seen DESC, times_seen DESC LIMIT ?"
    params.append(int(limit))
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def round_counts(round_date: str) -> dict:
    """How many of a round's sources were new vs already reported."""
    init_db()
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM events WHERE rounds LIKE ?",
            (f"%{round_date}%",),
        ).fetchone()[0]
        repeat = conn.execute(
            "SELECT COUNT(*) FROM events WHERE rounds LIKE ? AND first_seen < ?",
            (f"%{round_date}%", round_date),
        ).fetchone()[0]
    return {"total": total, "new": max(0, total - repeat), "repeat": repeat}


def reported_block(days: int = 7, limit: int = 15) -> str:
    """Prompt-ready "already reported" list (empty string when nothing to add)."""
    try:
        events = recent_events(days=days, limit=limit)
    except sqlite3.Error:
        return ""
    if not events:
        return ""
    lines = [
        f"以下是最近 {days} 天已报道过的来源（共 {len(events)} 条）。"
        "请勿把同一链接再次当作「新发现」；若确有实质进展（价格、版本、"
        "政策等发生变化），请明确标注「更新」并说明变化点。",
    ]
    for event in events:
        ctx = (event.get("context") or "").strip()
        line = f"- {event.get('last_seen', '')} | {event.get('host', '')}"
        if ctx:
            line += f" | {ctx}"
        lines.append(line)
    return "\n".join(lines)


def stats() -> dict:
    init_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        hosts = conn.execute("SELECT COUNT(DISTINCT host) FROM events").fetchone()[0]
        repeat = conn.execute("SELECT COUNT(*) FROM events WHERE times_seen > 1").fetchone()[0]
        latest = conn.execute("SELECT MAX(last_seen) FROM events").fetchone()[0]
    return {"total": total, "hosts": hosts, "seen_again": repeat, "last_seen": latest or ""}
