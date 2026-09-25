"""Chunk-level embeddings and semantic search over the report index."""

from __future__ import annotations

import array
import hashlib
import logging
import math
import re
import time
from datetime import date
from typing import Callable, Iterable, Optional

from web.indexer import db as index_db

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 200

EmbedFn = Callable[[list[str]], list[list[float]]]


def chunk_text(text: str, size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into paragraph-aware chunks of ~``size`` characters."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text or "") if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for para in paragraphs:
        while len(para) > size:
            piece, para = para[:size], para[size - overlap:]
            if current:
                flush()
            chunks.append(piece)
        if not current:
            current = para
        elif len(current) + len(para) + 2 <= size:
            current = f"{current}\n\n{para}"
        else:
            flush()
            current = para
    flush()
    return chunks


def init_vectors() -> None:
    with index_db.connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS report_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                dim INTEGER NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                UNIQUE(path, chunk_index)
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_path ON report_chunks(path);
            CREATE TABLE IF NOT EXISTS chunk_meta (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL DEFAULT '',
                embedded_at REAL NOT NULL
            );
            """
        )


def _hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _encode(vector: list[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _decode(blob: bytes) -> list[float]:
    values = array.array("f")
    values.frombytes(blob)
    return list(values)


def needs_embedding(path: str, content: str, model: str = "") -> bool:
    init_vectors()
    with index_db.connect() as conn:
        row = conn.execute(
            "SELECT content_hash, model FROM chunk_meta WHERE path = ?", (path,)
        ).fetchone()
    if not row:
        return True
    return row["content_hash"] != _hash(content) or row["model"] != model


def index_report(path: str, content: str, embed_fn: EmbedFn,
                 model: str = "", force: bool = False) -> int:
    """Embed and store chunks for one report. Returns chunk count."""
    if not force and not needs_embedding(path, content, model):
        return 0
    chunks = chunk_text(content)
    if not chunks:
        return 0
    vectors = embed_fn(chunks)
    if len(vectors) != len(chunks):
        raise ValueError("embedding count mismatch")

    init_vectors()
    with index_db.connect() as conn:
        conn.execute("DELETE FROM report_chunks WHERE path = ?", (path,))
        for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
            conn.execute(
                "INSERT INTO report_chunks (path, chunk_index, content, embedding,"
                " dim, model) VALUES (?,?,?,?,?,?)",
                (path, i, chunk, _encode(vector), len(vector), model),
            )
        conn.execute(
            "INSERT INTO chunk_meta (path, content_hash, model, embedded_at)"
            " VALUES (?,?,?,?) ON CONFLICT(path) DO UPDATE SET"
            " content_hash=excluded.content_hash, model=excluded.model,"
            " embedded_at=excluded.embedded_at",
            (path, _hash(content), model, time.time()),
        )
    return len(chunks)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def semantic_search(query_vector: list[float], limit: int = 8) -> list[dict]:
    """Cosine-similarity search over stored chunks."""
    if not query_vector:
        return []
    init_vectors()
    with index_db.connect() as conn:
        rows = conn.execute(
            "SELECT path, chunk_index, content, embedding FROM report_chunks"
        ).fetchall()
    scored = [
        {
            "path": row["path"],
            "chunk_index": row["chunk_index"],
            "snippet": row["content"][:300],
            "score": _cosine(query_vector, _decode(row["embedding"])),
            "source": "vector",
        }
        for row in rows
    ]
    scored.sort(key=lambda item: -item["score"])
    return [item for item in scored[:limit] if item["score"] > 0]


# A report whose date we cannot read sits in the middle of the scale: it neither
# wins on recency nor gets punished for a date nobody recorded.
_UNKNOWN_RECENCY = 0.5


def _parse_report_date(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def report_dates(paths: Iterable[str]) -> dict[str, str]:
    """Report dates for candidate paths, fetched in a single query."""
    wanted = [p for p in dict.fromkeys(paths) if p]
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    with index_db.connect() as conn:
        rows = conn.execute(
            f"SELECT path, report_date FROM reports WHERE path IN ({placeholders})",
            wanted,
        ).fetchall()
    return {row["path"]: row["report_date"] or "" for row in rows}


def recency_factors(dates: dict[str, str], half_life_days: float) -> dict[str, float]:
    """Exponential decay in [0, 1], measured against the newest candidate.

    Measuring against the newest candidate rather than today keeps the spread
    usable when the corpus itself is old: ordering is identical, but the
    differences stay large enough for the weight to matter.
    """
    parsed = {path: _parse_report_date(value) for path, value in dates.items()}
    known = [value for value in parsed.values() if value is not None]
    if not known:
        return {path: _UNKNOWN_RECENCY for path in parsed}
    newest = max(known)
    if half_life_days <= 0:
        return {path: 1.0 for path in parsed}
    factors = {}
    for path, value in parsed.items():
        if value is None:
            factors[path] = _UNKNOWN_RECENCY
        else:
            age_days = max(0, (newest - value).days)
            factors[path] = 0.5 ** (age_days / half_life_days)
    return factors


def _fts_relevance(hits: list[dict]) -> None:
    """Turn BM25 scores into a 0..1 scale relative to the best candidate.

    BM25 is negative and unbounded, so it is divided by the strongest score in
    the set: the best hit becomes 1.0 and everything else keeps its relative
    strength. Ranking by position instead (1/(1+rank)) would claim the first
    hit is twice as relevant as the second, which is both untrue and large
    enough to drown out any freshness signal.
    """
    scored = [hit for hit in hits if hit.get("fts_score") is not None]
    for hit in hits:
        if "fts_score" not in hit:
            # Vector-only hit: no keyword relevance to report.
            hit["fts_relevance"] = 0.0
        elif hit["fts_score"] is None or not scored or min(
                h["fts_score"] for h in scored) >= 0:
            # LIKE hits have no BM25, and treating them as equally relevant
            # matches is more honest than inventing an order from row position.
            hit["fts_relevance"] = 1.0
        else:
            best = min(h["fts_score"] for h in scored)
            hit["fts_relevance"] = min(1.0, max(0.0, hit["fts_score"] / best))


def _vector_relevance(hits: list[dict]) -> None:
    """Normalise summed chunk cosines against the strongest vector hit."""
    scored = [hit for hit in hits if hit.get("vector_score")]
    if not scored:
        return
    best = max(hit["vector_score"] for hit in scored)
    for hit in hits:
        raw = hit.get("vector_score") or 0.0
        hit["vector_relevance"] = min(1.0, raw / best) if best > 0 else 0.0


def _apply_recency(hits: list[dict], dates: dict[str, str],
                   weight: float, half_life_days: float) -> list[dict]:
    """Blend relevance with a recency bonus, keeping both in the result.

    ``relevance`` is the sum of the per-modality normalised scores, so a report
    found by both FTS and vectors outranks one found by a single modality.

    Before the bonus is added, relevance is snapped to a grid whose step is
    ``weight``. Two hits within one step of each other count as equally
    relevant and the newer one wins; a hit that is more relevant than the
    weight threshold cannot be displaced by age. Snapping matters because a
    continuous blend lets a fresh near-miss overtake a clearly better match on
    any corpus whose BM25 scores are close together.
    """
    _fts_relevance(hits)
    _vector_relevance(hits)
    factors = (recency_factors({hit["path"]: dates.get(hit["path"], "") for hit in hits},
                               half_life_days) if weight > 0 else {})
    for hit in hits:
        hit["report_date"] = dates.get(hit["path"], "")
        relevance = (float(hit.get("fts_relevance", 0.0))
                     + float(hit.get("vector_relevance", 0.0)))
        hit["relevance"] = round(relevance, 6)
        hit["score"] = relevance
        if weight > 0:
            tiered = round(relevance / weight) * weight
            hit["recency"] = round(factors.get(hit["path"], _UNKNOWN_RECENCY), 6)
            hit["score"] = round(tiered + weight * hit["recency"], 6)
    return hits


def hybrid_search(query: str, query_vector: Optional[list[float]], limit: int = 8,
                  recency_weight: float = 0.0, half_life_days: float = 30.0,
                  overfetch: int = 3) -> list[dict]:
    """Merge FTS5 hits with semantic hits (path-level dedupe).

    ``recency_weight`` trades relevance against freshness: at 0 the ranking is
    pure relevance, at 0.25 a report one half-life newer gains a quarter of a
    relevance point. The candidate pool is overfetched so a fresher report that
    BM25 ranked low can still surface.
    """
    merged: dict[str, dict] = {}
    pool = max(1, overfetch) * limit

    for hit in index_db.search_reports(query, pool):
        merged[hit["path"]] = {
            "path": hit["path"],
            "title": hit.get("title") or "",
            "snippet": hit.get("snippet") or "",
            "fts_score": hit.get("fts_score"),
            "source": "fts",
        }

    for hit in semantic_search(query_vector or [], pool):
        existing = merged.get(hit["path"])
        if existing:
            existing["vector_score"] = hit["score"]
            existing["snippet"] = existing.get("snippet") or hit["snippet"]
            existing["source"] = "hybrid"
        else:
            merged[hit["path"]] = {
                "path": hit["path"],
                "title": "",
                "snippet": hit["snippet"],
                "vector_score": hit["score"],
                "source": "vector",
            }

    ranked = list(merged.values())
    dates = report_dates(hit["path"] for hit in ranked)
    _apply_recency(ranked, dates, recency_weight, half_life_days)
    ranked.sort(key=lambda item: -item["score"])
    return ranked[:limit]


def index_stats() -> dict:
    init_vectors()
    with index_db.connect() as conn:
        chunks = conn.execute("SELECT COUNT(*) AS c FROM report_chunks").fetchone()["c"]
        docs = conn.execute("SELECT COUNT(*) AS c FROM chunk_meta").fetchone()["c"]
    return {"documents": docs, "chunks": chunks}
