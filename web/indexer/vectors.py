"""Chunk-level embeddings and semantic search over the report index."""

from __future__ import annotations

import array
import hashlib
import logging
import math
import re
import time
from typing import Callable, Optional

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


def hybrid_search(query: str, query_vector: Optional[list[float]],
                  limit: int = 8) -> list[dict]:
    """Merge FTS5 hits with semantic hits (path-level dedupe)."""
    merged: dict[str, dict] = {}

    for rank, hit in enumerate(index_db.search_reports(query, limit * 2)):
        merged[hit["path"]] = {
            "path": hit["path"],
            "title": hit.get("title") or "",
            "snippet": hit.get("snippet") or "",
            "score": 1.0 / (1 + rank),
            "source": "fts",
        }

    for hit in semantic_search(query_vector or [], limit * 2):
        existing = merged.get(hit["path"])
        if existing:
            existing["score"] += hit["score"]
            existing["source"] = "hybrid"
        else:
            merged[hit["path"]] = {
                "path": hit["path"],
                "title": "",
                "snippet": hit["snippet"],
                "score": hit["score"],
                "source": "vector",
            }

    ranked = sorted(merged.values(), key=lambda item: -item["score"])
    return ranked[:limit]


def index_stats() -> dict:
    init_vectors()
    with index_db.connect() as conn:
        chunks = conn.execute("SELECT COUNT(*) AS c FROM report_chunks").fetchone()["c"]
        docs = conn.execute("SELECT COUNT(*) AS c FROM chunk_meta").fetchone()["c"]
    return {"documents": docs, "chunks": chunks}
