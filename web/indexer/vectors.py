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
                  overfetch: int = 3, max_chunks_per_report: int = 2) -> list[dict]:
    """Merge FTS5 recall with chunk-level precision.

    ``recency_weight`` trades relevance against freshness: at 0 the ranking is
    pure relevance, at 0.25 a report one half-life newer gains a quarter of a
    relevance point. The candidate pool is overfetched so a fresher report that
    BM25 ranked low can still surface.

    The merge used to be keyed by report path, which quietly destroyed the
    chunk index. ``reports_fts`` holds one row per report, so its snippet is a
    window around the document's *first* match -- and a report's opening block is
    its header, which is exactly what a question mentioning the stage name
    matches. Meanwhile the chunk hits were collapsed to one per path and lost.
    The net effect was that Q&A could not cite the passage that answered the
    question: asked which opportunities P7 ranked P0, it was handed six
    front-matter blocks and said so, correctly, twice.

    So candidates are now chunks. A report matched by FTS borrows the best
    matching chunk of its own text as the snippet, and a report that genuinely
    has nothing relevant keeps its FTS window rather than being dropped.
    """
    pool = max(1, overfetch) * limit
    fts_hits = index_db.search_reports(query, pool)
    chunk_hits = semantic_search(query_vector or [], pool * 2)

    # Best chunk per report, for the FTS hits to quote.
    best_chunk: dict[str, dict] = {}
    for hit in chunk_hits:
        best_chunk.setdefault(hit["path"], hit)

    merged: dict[tuple, dict] = {}
    for hit in fts_hits:
        chunk = best_chunk.get(hit["path"])
        key = (hit["path"], chunk.get("chunk_index") if chunk else None)
        merged[key] = {
            "path": hit["path"],
            "title": hit.get("title") or "",
            "snippet": (chunk or hit).get("snippet") or "",
            "fts_score": hit.get("fts_score"),
            "vector_score": chunk.get("score") if chunk else None,
            "chunk_index": chunk.get("chunk_index") if chunk else None,
            "source": "hybrid" if chunk else "fts",
        }

    per_path: dict[str, int] = {}
    for hit in chunk_hits:
        key = (hit["path"], hit.get("chunk_index"))
        if key in merged:
            per_path[hit["path"]] = per_path.get(hit["path"], 0) + 1
            continue
        if per_path.get(hit["path"], 0) >= max_chunks_per_report:
            continue
        entry = {
            "path": hit["path"],
            "title": "",
            "snippet": hit["snippet"],
            "vector_score": hit["score"],
            "chunk_index": hit.get("chunk_index"),
            "source": "vector",
        }
        # No "fts_score" key at all: _fts_relevance reads an absent key as
        # "no keyword relevance" (0.0) and an explicit None as a LIKE hit with
        # no BM25 (1.0). Setting it to None would credit a vector-only
        # candidate with a perfect keyword score it never earned.
        merged[key] = entry
        per_path[hit["path"]] = per_path.get(hit["path"], 0) + 1

    ranked = list(merged.values())
    dates = report_dates(hit["path"] for hit in ranked)
    _apply_recency(ranked, dates, recency_weight, half_life_days)
    ranked.sort(key=lambda item: -item["score"])
    return _cap_per_source(ranked, limit, max_chunks_per_report)


def _source_name(path: str) -> str:
    """The report's own filename, which is stable across rounds.

    ``practical_ai_intelligence/2026-09-26/07_product_content_opportunities.md``
    and the 09-25 copy of the same stage share a filename, and every round
    writes the same ten names. So this groups a report series without needing to
    know anything about the directory layout.
    """
    return (path or "").rsplit("/", 1)[-1]


def _cap_per_source(hits: list[dict], limit: int, per_source: int) -> list[dict]:
    """Take at most ``per_source`` hits from any one report series.

    A report's opening block restates its own title, scope and inputs, so it
    matches almost any question *about that report* while containing none of its
    findings. With one hit per round, four dated copies of the same header ate
    the entire context: asked which opportunities P7 ranked P0, the retriever
    returned four front-matter blocks and the model -- correctly -- said the
    material was insufficient. Twice.

    Capping by filename forces the pool to reach other passages instead.
    """
    if per_source <= 0:
        return hits[:limit]
    used: dict[str, int] = {}
    picked: list[dict] = []
    for hit in hits:
        name = _source_name(hit["path"])
        if used.get(name, 0) >= per_source:
            continue
        used[name] = used.get(name, 0) + 1
        picked.append(hit)
        if len(picked) >= limit:
            break
    return picked


def index_stats() -> dict:
    init_vectors()
    with index_db.connect() as conn:
        chunks = conn.execute("SELECT COUNT(*) AS c FROM report_chunks").fetchone()["c"]
        docs = conn.execute("SELECT COUNT(*) AS c FROM chunk_meta").fetchone()["c"]
    return {"documents": docs, "chunks": chunks}
