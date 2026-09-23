"""Tests for chunking, vector storage and hybrid search."""

from __future__ import annotations

import pytest

from web.indexer import db as index_db
from web.indexer import vectors


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(tmp_path / "reports.db"))
    index_db.init_db()
    vectors.init_vectors()
    return tmp_path


def fake_embed(texts):
    return [[1.0, 0.0] if "alpha" in t else [0.0, 1.0] for t in texts]


def test_chunk_text_basic():
    chunks = vectors.chunk_text("para one\n\npara two\n\npara three", size=100)
    assert chunks == ["para one\n\npara two\n\npara three"]
    assert vectors.chunk_text("") == []
    assert vectors.chunk_text("   ") == []


def test_chunk_text_splits_long_paragraphs():
    text = "x" * 250
    chunks = vectors.chunk_text(text, size=100, overlap=20)
    assert len(chunks) >= 3
    assert all(len(c) <= 100 for c in chunks)


def test_chunk_text_packs_and_splits_paragraphs():
    text = "\n\n".join(["a" * 60] * 5)
    chunks = vectors.chunk_text(text, size=130, overlap=10)
    assert len(chunks) == 3
    assert all(len(c) <= 130 for c in chunks)


def test_index_report_and_skip_unchanged(db):
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return fake_embed(texts)

    n = vectors.index_report("a.md", "alpha content", embed, model="m")
    assert n == 1 and len(calls) == 1

    assert vectors.index_report("a.md", "alpha content", embed, model="m") == 0
    assert len(calls) == 1

    vectors.index_report("a.md", "changed alpha", embed, model="m")
    assert len(calls) == 2

    vectors.index_report("a.md", "changed alpha", embed, model="m2")
    assert len(calls) == 3


def test_semantic_search_ranks_by_similarity(db):
    vectors.index_report("a.md", "alpha doc", fake_embed, model="m")
    vectors.index_report("b.md", "beta doc", fake_embed, model="m")

    hits = vectors.semantic_search([1.0, 0.0], limit=5)
    assert [h["path"] for h in hits] == ["a.md"]
    assert hits[0]["score"] == pytest.approx(1.0)

    beta = vectors.semantic_search([0.0, 1.0], limit=5)
    assert [h["path"] for h in beta] == ["b.md"]


def test_hybrid_search_merges_fts_and_vector(db):
    index_db.upsert_report("a.md", mtime=1.0, title="Alpha", content="alpha words")
    index_db.upsert_report("b.md", mtime=1.0, title="Beta", content="beta words")
    vectors.index_report("a.md", "alpha words", fake_embed, model="m")
    vectors.index_report("b.md", "beta words", fake_embed, model="m")

    hits = vectors.hybrid_search("alpha", [1.0, 1.0], limit=5)
    by_path = {h["path"]: h for h in hits}
    assert "a.md" in by_path and "b.md" in by_path
    assert by_path["a.md"]["source"] == "hybrid"
    assert by_path["b.md"]["source"] == "vector"
    assert by_path["a.md"]["score"] > by_path["b.md"]["score"]


def test_index_stats(db):
    vectors.index_report("a.md", "alpha", fake_embed, model="m")
    stats = vectors.index_stats()
    assert stats == {"documents": 1, "chunks": 1}
