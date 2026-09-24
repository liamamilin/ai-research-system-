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
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

from core.provenance import extract_urls, normalize_url

DB_FILE = os.path.join("state", "events.db")

CONTEXT_CHARS = 140
MAX_TITLE_CHARS = 90
MAX_ROUNDS_KEPT = 8
CLUSTER_WINDOW_DAYS = 21
CLUSTER_MIN_JACCARD = 0.6
CLUSTER_MIN_DISTINCTIVE = 2
CLUSTER_MIN_UNION = 3
_URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>()\[\]\"'，。；：、）】《》]+")
_MARKDOWN_NOISE_RE = re.compile(r"[|*_#>`~\[\]]+")
_TOKEN_SPLIT_RE = re.compile(r"[^0-9a-z一-鿿]+")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s*")
_DATE_ONLY_RE = re.compile(r"^[\s（）()\[\]【】]*"
                           r"(?:\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"
                           r"|\d{1,2}[-/月]\d{1,2}日?"
                           r"|访问于[^ ]*|发布日期未知|未知日期)"
                           r"[\s（）()\[\]【】]*$")

# Stop words: grammar-level noise, present in virtually every slug.
_SLUG_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "at", "by",
    "with", "from", "is", "are", "was", "were", "be", "as", "it", "its", "this",
    "that", "we", "you", "how", "why", "what", "when", "who", "can", "will",
}

# Section words: shared by unrelated pages ("/release-notes" vs "/changelog"),
# so they can never establish a cluster on their own.
_GENERIC_SLUG_TOKENS = {
    "release", "releases", "notes", "note", "changelog", "changes", "change",
    "log", "docs", "doc", "documentation", "blog", "news", "guide", "guides",
    "overview", "pricing", "price", "update", "updates", "updating", "whats",
    "new", "latest", "feature", "features", "api", "reference", "references",
    "homepage", "index", "page", "pages", "about", "support", "help", "faq",
    "list", "all", "more", "read", "view", "post", "posts", "article",
    "articles", "story", "stories", "report", "reports", "video", "watch",
    "official", "site", "web", "com", "www", "html", "htm", "php", "asp",
}

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
        _migrate_cluster_columns(conn)


def _migrate_cluster_columns(conn: sqlite3.Connection) -> None:
    """Add the clustering columns, then backfill them for known sources."""
    existing = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
    additions = {
        "event_key": "TEXT NOT NULL DEFAULT ''",   # canonical url_key when a duplicate
        "title": "TEXT NOT NULL DEFAULT ''",       # best human label
        "title_key": "TEXT NOT NULL DEFAULT ''",   # normalized title for exact match
        "tokens": "TEXT NOT NULL DEFAULT ''",      # space-separated URL signature
    }
    for column, decl in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE events ADD COLUMN {column} {decl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_event_key ON events(event_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_first_seen ON events(first_seen)")

    stale = conn.execute(
        "SELECT url_key, url, context FROM events WHERE tokens = ''").fetchall()
    for row in stale:
        label = _title_label(row["context"], row["url_key"])
        conn.execute(
            "UPDATE events SET tokens = ?, title = ?, title_key = ? WHERE url_key = ?",
            (" ".join(sorted(slug_signature(row["url_key"]))), label,
             normalize_title(label), row["url_key"]),
        )
    # Backfill clusters for sources registered before this feature existed.
    if conn.execute("SELECT COUNT(*) FROM events WHERE event_key != ''").fetchone()[0] == 0:
        _cluster_existing(conn)


def slug_signature(url_key: str, limit: int = 24) -> frozenset:
    """Distinctive tokens from a URL path, used to spot the same story elsewhere."""
    if not url_key:
        return frozenset()
    try:
        parsed = urlparse(url_key)
        raw = unquote(f"{parsed.path} {parsed.query}".strip("/"))
    except ValueError:
        raw = url_key
    tokens = set()
    for token in _TOKEN_SPLIT_RE.split(raw.lower()):
        if len(token) < 3 or token.isdigit() or token in _SLUG_STOPWORDS:
            continue
        if _YEAR_RE.match(token):
            continue
        tokens.add(token)
    if len(tokens) > limit:
        # Keep the longest tokens: long ones carry the entity names.
        tokens = set(sorted(tokens, key=len, reverse=True)[:limit])
    return frozenset(tokens)


def normalize_title(label: str) -> str:
    """Comparable form of a title: no punctuation, no case, no whitespace."""
    return re.sub(r"[\W_]+", "", (label or "").lower())


def slug_title(url_key: str) -> str:
    """A readable label from the URL path.

    Used when the report gave no usable label (bare host, date-only cell):
    news slugs are usually the headline, e.g. "/openai-caught-in-data-breach".
    Walks the path from the end and takes the first segment that still has two
    meaningful words, so "/pypi/ruff/json" falls back to "pypi ruff" instead of
    "json".
    """
    try:
        path = unquote(urlparse(url_key or "").path)
    except ValueError:
        return ""
    segments = [seg for seg in re.split(r"/+", path) if seg]
    if not segments:
        return ""
    for segment in reversed(segments):
        clean = re.sub(r"\.(html?|php|aspx?|pdf|md)$", "", segment, flags=re.I)
        words = [w for w in re.split(r"[-_+%.,]+", clean)
                 if len(w) >= 2 and not w.isdigit()
                 and w.lower() not in _SLUG_STOPWORDS
                 and w.lower() not in _GENERIC_SLUG_TOKENS]
        if len(words) < 2:
            continue
        title = " ".join(words[:8]).strip(" -_")
        if 4 <= len(title) <= 80:
            return title
    return ""


def _title_label(context: str, url_key: str = "") -> str:
    """Turn a citation's context into a title, or "" when it carries no signal.

    Reference tables often label a link with just the host or a date
    ("arXiv", "（2026-09-10）"), or with a row like
    "证据来源 ： （2026-09-15）| 一手 | <url>"; those must not become titles.
    Long labels are cut to their first clause so the prompt block stays readable.
    """
    generic = {g.strip(" :：-").lower() for g in GENERIC_LABELS}
    candidates: list = []
    if context and "|" in context:
        cells = [c.strip() for c in context.split("|") if c.strip()]
        useful = [
            c for c in cells
            if c.strip(" :：-").lower() not in generic
            and not _DATE_ONLY_RE.match(c)
            and len(normalize_title(c)) >= 4
        ]
        candidates = useful or cells
    else:
        candidates = [context or ""]

    text = ""
    for candidate in candidates:
        cleaned = _LEADING_NUMBER_RE.sub("", candidate.strip())
        cleaned = _MARKDOWN_NOISE_RE.sub(" ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" -–—:：;；")
        if not cleaned or _DATE_ONLY_RE.match(cleaned):
            continue
        if "." in cleaned and len(cleaned.split()[0]) <= 4 and "/" in cleaned.split()[0]:
            continue  # bare hostname
        if cleaned.strip(" :：-").lower() in generic:
            continue
        if re.split(r"[/／]", cleaned)[0].strip(" :：-").lower() in generic:
            continue
        if len(cleaned.split()) == 1:
            continue  # a bare site/brand name is not a title
        if len(normalize_title(cleaned)) < 4:
            continue
        text = cleaned
        break

    if not text:
        return slug_title(url_key)
    if len(text) > MAX_TITLE_CHARS:
        head = text[:MAX_TITLE_CHARS]
        cut = max((head.rfind(ch) for ch in "。；！？.!?;，,"), default=-1)
        text = (head[:cut] if cut >= 20 else head).rstrip("，,、 ") + "…"
    return text


def _distinctive(tokens: Iterable[str]) -> set:
    return {t for t in tokens if t not in _GENERIC_SLUG_TOKENS}


def same_story(left: frozenset, right: frozenset,
               same_host: bool = False) -> bool:
    """Whether two URL signatures describe the same story.

    Deliberately conservative: same-host links are never merged (one site
    covering a story twice is a different problem), generic section words
    cannot carry a match, and at least two distinctive tokens must overlap.
    """
    if same_host or not left or not right:
        return False
    shared = left & right
    if not shared:
        return False
    union = left | right
    if len(union) < CLUSTER_MIN_UNION:
        return False
    if len(_distinctive(shared)) < CLUSTER_MIN_DISTINCTIVE:
        return False
    return len(shared) / len(union) >= CLUSTER_MIN_JACCARD


def _day_number(date: str) -> Optional[int]:
    try:
        return int(date[:4]) * 372 + int(date[5:7]) * 31 + int(date[8:10])
    except (TypeError, ValueError):
        return None


def _within_window(a: str, b: str, days: int = CLUSTER_WINDOW_DAYS) -> bool:
    da, db = _day_number(a), _day_number(b)
    if da is None or db is None:
        return False
    return abs(da - db) <= days


def _parse_tokens(value: str) -> frozenset:
    return frozenset(t for t in (value or "").split() if t)


def _host(url_key: str) -> str:
    return url_key.split("/", 3)[2] if url_key.count("/") >= 2 else url_key


def _label_for(text: str, url: str) -> str:
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


def _context_for(text: str, url: str) -> str:
    return _label_for(text, url)


def _find_cluster(conn: sqlite3.Connection, url_key: str, tokens: frozenset,
                  host: str, date: str) -> Optional[str]:
    """Canonical url_key of the event this source belongs to, if any."""
    if not tokens:
        return None
    # Symmetric window: a source from three weeks later is a development, and a
    # source from months earlier must never be folded into a newer event.
    rows = conn.execute(
        "SELECT url_key, host, tokens, first_seen, event_key FROM events"
        " WHERE event_key = '' AND host != ?"
        " AND first_seen BETWEEN date(? , ?) AND date(?)",
        (host, date, f"-{CLUSTER_WINDOW_DAYS} days", date),
    ).fetchall()
    for row in rows:
        other = _parse_tokens(row["tokens"])
        if not _within_window(row["first_seen"], date):
            continue
        if same_story(tokens, other, same_host=(row["host"] == host)):
            # Canonical rows carry an empty event_key; they are their own key.
            return row["event_key"] or row["url_key"]
    return None


def _cluster_existing(conn: sqlite3.Connection) -> int:
    """Group historical sources that describe the same story."""
    rows = conn.execute(
        "SELECT url_key, host, tokens, first_seen FROM events ORDER BY first_seen"
    ).fetchall()
    clustered = 0
    for row in rows:
        tokens = _parse_tokens(row["tokens"])
        if not tokens:
            continue
        canonical = _find_cluster(conn, row["url_key"], tokens, row["host"], row["first_seen"])
        if canonical and canonical != row["url_key"]:
            conn.execute("UPDATE events SET event_key = ? WHERE url_key = ?",
                         (canonical, row["url_key"]))
            clustered += 1
    return clustered


def _append(value: str, item: str, cap: int = MAX_ROUNDS_KEPT) -> str:
    items = [v for v in (value or "").split(",") if v]
    if item and item not in items:
        items.append(item)
    return ",".join(items[-cap:])


def register_report(job: str, date: str, content: str,
                    round_date: Optional[str] = None) -> dict:
    """Register every URL a report cited. Returns per-round counts.

    Sources that describe a story already in memory are attached to that event
    instead of becoming new events, so the next round does not see the same
    story under a different outlet's URL as a fresh finding.
    """
    init_db()
    urls = extract_urls(content or "")
    if not urls:
        return {"new": 0, "repeat": 0, "total": 0, "clustered": 0}

    new = repeat = clustered = 0
    with _lock, connect() as conn:
        for url in urls:
            key = normalize_url(url)
            if not key:
                continue
            row = conn.execute("SELECT * FROM events WHERE url_key = ?", (key,)).fetchone()
            if row is None:
                label = _label_for(content, url)
                title = _title_label(label, key)
                tokens = slug_signature(key)
                canonical = _find_cluster(conn, key, tokens, _host(key), date)
                conn.execute(
                    "INSERT INTO events (url_key, url, host, context, first_seen,"
                    " last_seen, times_seen, jobs, rounds, event_key, title,"
                    " title_key, tokens)"
                    " VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?)",
                    (key, url, _host(key), label, date, date, job,
                     round_date or "", canonical or "", title,
                     normalize_title(title), " ".join(sorted(tokens))),
                )
                if canonical:
                    clustered += 1
                else:
                    new += 1
                continue
            already_this_round = bool(round_date) and round_date in (row["rounds"] or "").split(",")
            conn.execute(
                "UPDATE events SET last_seen = ?, times_seen = times_seen + ?,"
                " context = CASE WHEN ? = '' THEN context ELSE ? END,"
                " jobs = ?, rounds = ? WHERE url_key = ?",
                (date, 0 if already_this_round else 1,
                 _label_for(content, url), _label_for(content, url),
                 _append(row["jobs"], job, 5), _append(row["rounds"], round_date or ""), key),
            )
            if not already_this_round:
                repeat += 1
    return {"new": new, "repeat": repeat, "total": len(urls), "clustered": clustered}


def recent_events(days: int = 7, limit: int = 50,
                  before: Optional[str] = None) -> list[dict]:
    """Events seen within the last ``days`` days, most recent first.

    One entry per story: sources folded into an earlier event are listed as
    corroborating hosts instead of appearing as separate events.
    """
    init_db()
    cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    query = "SELECT * FROM events WHERE event_key = '' AND last_seen >= ?"
    params: list = [cutoff]
    if before:
        query += " AND first_seen < ?"
        params.append(before)
    query += " ORDER BY last_seen DESC, times_seen DESC LIMIT ?"
    params.append(int(limit))
    with connect() as conn:
        events = [dict(r) for r in conn.execute(query, params).fetchall()]
        keys = [e["url_key"] for e in events]
        if keys:
            placeholders = ",".join("?" * len(keys))
            duplicates = conn.execute(
                f"SELECT event_key, host, url, last_seen FROM events"
                f" WHERE event_key IN ({placeholders})",
                keys,
            ).fetchall()
            by_event: dict = {}
            for row in duplicates:
                by_event.setdefault(row["event_key"], []).append(
                    {"host": row["host"], "url": row["url"], "last_seen": row["last_seen"]})
            for event in events:
                sources = by_event.get(event["url_key"], [])
                event["sources"] = len(sources)
                event["also_reported_by"] = sorted({s["host"] for s in sources if s["host"]})
                event["source_urls"] = [s["url"] for s in sources]
    return events


def cluster_stats() -> dict:
    """How many known sources were folded into an existing event."""
    init_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        grouped = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_key != ''").fetchone()[0]
        events = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_key = ''").fetchone()[0]
    return {"sources": total, "events": events, "clustered_sources": grouped}


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
        clustered = conn.execute(
            "SELECT COUNT(*) FROM events WHERE rounds LIKE ? AND event_key != ''",
            (f"%{round_date}%",),
        ).fetchone()[0]
    return {"total": total, "new": max(0, total - repeat), "repeat": repeat,
            "clustered": clustered}


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
        title = (event.get("title") or event.get("context") or "").strip()
        line = f"- {event.get('last_seen', '')} | {event.get('host', '')}"
        if title:
            line += f" | {title}"
        others = event.get("also_reported_by") or []
        if others:
            line += f"（另见：{'、'.join(others[:4])}）"
        lines.append(line)
    return "\n".join(lines)


def stats() -> dict:
    init_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        hosts = conn.execute("SELECT COUNT(DISTINCT host) FROM events").fetchone()[0]
        repeat = conn.execute("SELECT COUNT(*) FROM events WHERE times_seen > 1").fetchone()[0]
        latest = conn.execute("SELECT MAX(last_seen) FROM events").fetchone()[0]
        clustered = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_key != ''").fetchone()[0]
    return {"total": total, "hosts": hosts, "seen_again": repeat,
            "last_seen": latest or "", "clustered_sources": clustered,
            "events": total - clustered}
