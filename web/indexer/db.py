"""SQLite + FTS5 schema for the reports index."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

_DB_PATH_OVERRIDE: str | None = None
_lock = threading.Lock()

# Trigram indexing makes Chinese/Japanese full-text search work: unicode61
# treats a run of CJK characters as a single token, so "独立游戏" never matches
# a report that writes "独立 游戏的发行". Queries with terms shorter than
# three characters cannot use trigrams and fall back to a LIKE scan.
FTS_TOKENIZER = "trigram"
FTS_SCHEMA_VERSION = 2
MIN_TRIGRAM_QUERY = 3
# A query can be long; only the first few short terms get a LIKE scan so a
# pasted paragraph cannot build an unbounded SQL statement.
MAX_LIKE_TERMS = 8


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


_EXTRA_COLUMNS = (
    ("favorite", "INTEGER NOT NULL DEFAULT 0"),
    ("tags", "TEXT NOT NULL DEFAULT ''"),
    ("read_at", "REAL"),
    ("rating", "INTEGER NOT NULL DEFAULT 0"),
    ("rating_note", "TEXT NOT NULL DEFAULT ''"),
    # The report's own date, not the file's mtime: a backfill run rewrites
    # mtime for old reports, which would make June look like today.
    ("report_date", "TEXT NOT NULL DEFAULT ''"),
)


def _ensure_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(reports)")}
    for name, ddl in _EXTRA_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE reports ADD COLUMN {name} {ddl}")
    # Backfill rows written before the column existed (or before this run added
    # it — `existing` was read before the ALTERs above).
    stale = conn.execute(
        "SELECT path, mtime FROM reports WHERE report_date = ''").fetchall()
    for row in stale:
        conn.execute("UPDATE reports SET report_date = ? WHERE path = ?",
                     (report_date_for(row["path"], row["mtime"]), row["path"]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reports_date ON reports(report_date)")


_DATE_IN_PATH_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")


def report_date_for(path: str, mtime: float = 0.0) -> str:
    """The report's own date: the one in its path, else its mtime's date.

    Pipeline output encodes the round date in the path; older ad-hoc reports do
    not, so they fall back to the file date. Comparing dates as strings is safe
    for ISO format and keeps the filter a plain indexed range scan.
    """
    match = _DATE_IN_PATH_RE.search(path or "")
    if match:
        return match.group(1)
    if mtime:
        return time.strftime("%Y-%m-%d", time.localtime(mtime))
    return ""


def init_db():
    """Create tables and FTS5 virtual table if they don't exist."""
    rebuilt = False
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

    CREATE INDEX IF NOT EXISTS idx_reports_mtime ON reports(mtime);
    CREATE INDEX IF NOT EXISTS idx_reports_category ON reports(category);
    """
        )
        _ensure_columns(conn)
        rebuilt = _ensure_fts_table(conn)
    return {"fts_rebuilt": rebuilt}


def _ensure_fts_table(conn) -> bool:
    """Create (or migrate) the FTS5 table. Returns True when it was rebuilt."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='reports_fts'"
    ).fetchone()
    expected = f"tokenize='{FTS_TOKENIZER}'"
    if row is not None and row["sql"] and expected in row["sql"]:
        return False

    had_table = row is not None
    if had_table:
        conn.execute("DROP TABLE reports_fts")
    conn.execute(
        f"CREATE VIRTUAL TABLE reports_fts USING fts5(title, content, tokenize='{FTS_TOKENIZER}')"
    )
    conn.execute(f"PRAGMA user_version = {FTS_SCHEMA_VERSION}")
    return had_table


def _serialize_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    try:
        data["tags"] = json.loads(data.get("tags") or "[]")
    except (ValueError, TypeError):
        data["tags"] = []
    data["favorite"] = bool(data.get("favorite"))
    data["read"] = bool(data.get("read_at"))
    return data


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
            INSERT INTO reports (path, job_name, size_bytes, mtime, title, category,
                                 indexed_at, report_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                mtime=excluded.mtime,
                size_bytes=excluded.size_bytes,
                job_name=excluded.job_name,
                title=excluded.title,
                category=excluded.category,
                indexed_at=excluded.indexed_at,
                report_date=excluded.report_date
            """,
            (path, job_name, size_bytes, mtime, title, category, now,
             report_date_for(path, mtime)),
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


def indexed_paths() -> set[str]:
    """Every report path currently in the index."""
    with connect() as conn:
        return {row["path"] for row in conn.execute("SELECT path FROM reports")}


def prune_missing(output_dir: str) -> int:
    """Drop index rows whose report file no longer exists on disk.

    Reconciliation pass: filesystem watchers can coalesce or miss delete
    events, and a stale row would surface a report the user cannot open.
    """
    with connect() as conn:
        paths = [r["path"] for r in conn.execute("SELECT path FROM reports")]
    stale = [p for p in paths if not os.path.isfile(os.path.join(output_dir, p))]
    for path in stale:
        remove_report(path)
    return len(stale)


def prune_fts_orphans() -> int:
    """Drop FTS rows whose report row is gone.

    FTS rows are keyed by the reports rowid, so a delete that goes through
    remove_report keeps them in step. Anything that removes rows from `reports`
    by other means (a manual cleanup, a restored backup, the FTS trigram
    migration) leaves search hits that resolve to no report at all.
    """
    with connect() as conn:
        try:
            orphans = [r[0] for r in conn.execute(
                "SELECT rowid FROM reports_fts"
                " WHERE rowid NOT IN (SELECT rowid FROM reports)")]
            for rowid in orphans:
                conn.execute("DELETE FROM reports_fts WHERE rowid = ?", (rowid,))
        except sqlite3.OperationalError:
            # No FTS table yet (fresh database).
            return 0
    return len(orphans)


def remove_report(path: str):
    """Delete a report entry."""
    with connect() as conn:
        row = conn.execute("SELECT rowid FROM reports WHERE path = ?", (path,)).fetchone()
        if row:
            conn.execute("DELETE FROM reports_fts WHERE rowid = ?", (row["rowid"],))
        conn.execute("DELETE FROM reports WHERE path = ?", (path,))


def sanitize_fts_query(query: str) -> str:
    """Quote each term so FTS5 operators (-, :, *, etc.) can't break the query."""
    tokens = [t for t in (query or "").split() if t]
    return " ".join('"' + t.replace('"', '""') + '"' for t in tokens)


def search_reports(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across report titles and content using FTS5.

    Trigrams need three characters per term, so short terms are searched with a
    LIKE scan over titles and paths. Both halves run for a mixed query: sending
    "AI agent" down the LIKE path as one phrase threw away the trigram search
    that "agent" could have used, and the whole query then matched nothing.
    """
    text = (query or "").strip()
    if not text:
        return []
    terms = [t for t in text.split() if t]
    short = [t for t in terms if len(t) < MIN_TRIGRAM_QUERY]
    searchable = [t for t in terms if len(t) >= MIN_TRIGRAM_QUERY]

    if not searchable:
        return _search_like(short or terms, limit)

    rows = _search_fts(" ".join(searchable), limit)
    if not short:
        return rows
    return _merge_paths(rows, _search_like(short, limit), limit)


def _search_fts(text: str, limit: int) -> list[dict]:
    """Trigram FTS5 search, best match first."""
    safe_query = sanitize_fts_query(text)
    if not safe_query:
        return []
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
                r.favorite,
                r.tags,
                r.read_at,
                snippet(reports_fts, 1, '<mark>', '</mark>', '...', 40) AS snippet,
                bm25(reports_fts) AS fts_score
            FROM reports_fts
            JOIN reports r ON r.rowid = reports_fts.rowid
            WHERE reports_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (safe_query, limit),
        ).fetchall()
        return [_serialize_row(r) for r in rows]


def _merge_paths(primary: list[dict], extra: list[dict], limit: int) -> list[dict]:
    """Keep the primary ranking, then append hits the primary did not find."""
    seen = {row["path"] for row in primary}
    out = list(primary)
    for row in extra:
        if row["path"] in seen:
            continue
        seen.add(row["path"])
        out.append(row)
    return out[:limit]


def _search_like(terms: list[str], limit: int) -> list[dict]:
    """Substring search for terms too short for trigram indexing.

    Trigrams cannot match one- or two-character terms, and the body text only
    lives in the FTS index, so short queries match titles and paths only. Terms
    are OR-ed: matching the whole phrase at once would make "AI 检索" miss a
    report titled "AI 工程" purely because of the second word.
    """
    wanted = [t for t in terms if t][:MAX_LIKE_TERMS]
    if not wanted:
        return []
    clause = " OR ".join(["(title LIKE ? COLLATE NOCASE OR path LIKE ? COLLATE NOCASE)"]
                         * len(wanted))
    params: list = []
    for term in wanted:
        pattern = f"%{term.replace('%', '').replace('_', '')}%"
        params.extend([pattern, pattern])
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT path, job_name, title, category, size_bytes, mtime,
                   favorite, tags, read_at
            FROM reports
            WHERE {clause}
            ORDER BY mtime DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    out = []
    for row in rows:
        item = _serialize_row(row)
        item["snippet"] = None
        # A LIKE hit carries no gradation of relevance: every row matched the
        # same substring. Reporting None keeps the caller from inventing a
        # ranking out of row order.
        item["fts_score"] = None
        out.append(item)
    return out


def get_report(path: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM reports WHERE path = ?", (path,)).fetchone()
    return _serialize_row(row) if row else None


def set_report_meta(
    path: str,
    favorite: Optional[bool] = None,
    tags: Optional[list[str]] = None,
    read: Optional[bool] = None,
    rating: Optional[int] = None,
    rating_note: Optional[str] = None,
) -> Optional[dict]:
    """Update favorite / tags / read / rating state of one report."""
    import time

    fields: list[str] = []
    params: list = []
    if favorite is not None:
        fields.append("favorite = ?")
        params.append(1 if favorite else 0)
    if tags is not None:
        cleaned = sorted({t.strip() for t in tags if t and t.strip()})
        fields.append("tags = ?")
        params.append(json.dumps(cleaned, ensure_ascii=False))
    if read is not None:
        fields.append("read_at = ?")
        params.append(time.time() if read else None)
    if rating is not None:
        fields.append("rating = ?")
        params.append(max(0, min(int(rating), 5)))
    if rating_note is not None:
        fields.append("rating_note = ?")
        params.append(rating_note[:500])

    if not fields:
        return get_report(path)

    with connect() as conn:
        cur = conn.execute(
            f"UPDATE reports SET {', '.join(fields)} WHERE path = ?",
            params + [path],
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM reports WHERE path = ?", (path,)).fetchone()
    return _serialize_row(row)


def rating_summary() -> dict:
    """Average report rating overall and per job."""
    with connect() as conn:
        overall = conn.execute(
            "SELECT COUNT(*) AS cnt, AVG(rating) AS avg FROM reports WHERE rating > 0"
        ).fetchone()
        per_job = conn.execute(
            "SELECT job_name, COUNT(*) AS cnt, AVG(rating) AS avg FROM reports"
            " WHERE rating > 0 AND job_name != '' GROUP BY job_name"
            " ORDER BY avg DESC"
        ).fetchall()
    return {
        "count": overall["cnt"] or 0,
        "average": round(overall["avg"], 2) if overall["avg"] is not None else None,
        "per_job": [
            {"job_name": r["job_name"], "count": r["cnt"],
             "average": round(r["avg"], 2)}
            for r in per_job
        ],
    }


def list_tags() -> list[dict]:
    """All tags with usage counts."""
    counts: dict[str, int] = {}
    with connect() as conn:
        rows = conn.execute("SELECT tags FROM reports WHERE tags != ''").fetchall()
    for row in rows:
        try:
            for tag in json.loads(row["tags"]):
                counts[tag] = counts.get(tag, 0) + 1
        except (ValueError, TypeError):
            continue
    return [{"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


SORT_ORDERS = {
    "recent": "report_date DESC, mtime DESC",
    "oldest": "report_date ASC, mtime ASC",
    "title": "title COLLATE NOCASE ASC",
    "size": "size_bytes DESC",
    # Citation coverage lives in the per-report meta sidecar, not in this table;
    # the route sorts those in Python after fetching.
    "coverage": "report_date DESC, mtime DESC",
}


def _filter_clause(
    category: str | None = None,
    job_name: str | None = None,
    favorite: bool | None = None,
    tag: str | None = None,
    unread: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    latest_round: bool = False,
) -> tuple[list[str], list]:
    """Shared WHERE fragments for the list and the tree, so they cannot drift."""
    where_parts: list[str] = []
    params: list = []

    if category:
        where_parts.append("category = ?")
        params.append(category)
    if job_name:
        where_parts.append("job_name = ?")
        params.append(job_name)
    if favorite is not None:
        where_parts.append("favorite = ?")
        params.append(1 if favorite else 0)
    if tag:
        where_parts.append("tags LIKE ?")
        params.append(f"%{json.dumps(tag, ensure_ascii=False)}%")
    if unread is not None:
        where_parts.append("read_at IS " + ("NULL" if unread else "NOT NULL"))
    if since:
        where_parts.append("report_date >= ?")
        params.append(since)
    if until:
        where_parts.append("report_date <= ?")
        params.append(until)
    if latest_round:
        round_prefix = latest_round_prefix()
        if round_prefix:
            # Date alone would also pull in unrelated reports written the same
            # day; a round is one directory's output.
            where_parts.append("report_date = ? AND path LIKE ?")
            params.extend([latest_round_date(), round_prefix + "%"])
    return where_parts, params


def latest_round_prefix() -> str:
    """Directory of the newest pipeline round, e.g. practical_ai_intelligence/2026-09-24/."""
    date = latest_round_date()
    if not date:
        return ""
    with connect() as conn:
        row = conn.execute(
            "SELECT path FROM reports WHERE report_date = ? AND path GLOB '*/09_*'"
            " ORDER BY path LIMIT 1", (date,)).fetchone()
    if not row:
        return ""
    return row["path"].rsplit("/", 1)[0] + "/"


def latest_round_date() -> str:
    """The newest report date, preferring a real pipeline round.

    A round is a date whose reports include a 09_* synthesis file; a day with a
    single ad-hoc report is not something to call "the latest round".
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT report_date FROM reports WHERE report_date != ''"
            " ORDER BY report_date DESC").fetchall()
        for candidate in [r["report_date"] for r in row][:14]:
            # GLOB, not LIKE: SQLite's LIKE has no [...] character class, so
            # the usual "[_]" trick matches nothing at all.
            has_synthesis = conn.execute(
                "SELECT 1 FROM reports WHERE report_date = ? AND path GLOB '*/09_*'"
                " LIMIT 1", (candidate,)).fetchone()
            if has_synthesis:
                return candidate
        return row[0]["report_date"] if row else ""


def list_reports(
    page: int = 1,
    per_page: int = 20,
    category: str | None = None,
    job_name: str | None = None,
    favorite: bool | None = None,
    tag: str | None = None,
    unread: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    latest_round: bool = False,
    sort: str = "recent",
) -> dict:
    """Paginated list of reports with optional filters."""
    where_parts, params = _filter_clause(
        category, job_name, favorite, tag, unread, since, until, latest_round)

    if category:
        where_parts.append("category = ?")
        params.append(category)
    if job_name:
        where_parts.append("job_name = ?")
        params.append(job_name)
    if favorite is not None:
        where_parts.append("favorite = ?")
        params.append(1 if favorite else 0)
    if tag:
        where_parts.append("tags LIKE ?")
        params.append(f"%{json.dumps(tag, ensure_ascii=False)}%")
    if unread is not None:
        where_parts.append("read_at IS " + ("NULL" if unread else "NOT NULL"))

    where = ""
    if where_parts:
        where = " WHERE " + " AND ".join(where_parts)

    order = SORT_ORDERS.get(sort, SORT_ORDERS["recent"])
    with connect() as conn:
        count_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM reports{where}", params
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"SELECT * FROM reports{where} ORDER BY {order} LIMIT ? OFFSET ?",
            params + [per_page, offset],
        ).fetchall()

    return {
        "items": [_serialize_row(r) for r in rows],
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


def get_report_tree(
    category: str | None = None,
    job_name: str | None = None,
    favorite: bool | None = None,
    tag: str | None = None,
    unread: bool | None = None,
    since: str | None = None,
    until: str | None = None,
    latest_round: bool = False,
) -> list[dict]:
    """Nested directory tree, filtered and pruned to the reports that matched.

    The tree has to follow the same filters as the list: showing a tree that
    still lists every directory next to a 24-report list would claim 300 when
    there are 24. Directories carry a count so the tree also answers "how much
    is in here".
    """
    where_parts, params = _filter_clause(
        category, job_name, favorite, tag, unread, since, until, latest_round)
    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT path, title, category, size_bytes, mtime FROM reports{where}"
            f" ORDER BY path", params,
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
                count = sum(c.get("count", 1) for c in children)
                if not children:
                    return []  # nothing matched inside: do not show the branch
                entry: dict = {"name": key, "type": "dir", "children": children,
                               "count": count}
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
