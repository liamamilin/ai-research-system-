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


def _index_on(path, day, title, content):
    """Index a report whose report_date comes from the path, not from mtime."""
    index_db.upsert_report(f"output/{day}/{path}", mtime=1.0, title=title,
                           content=content)


def test_recency_factors_decay_by_half_life():
    dates = {"new.md": "2026-09-25", "half.md": "2026-08-26", "old.md": "2026-06-26"}
    factors = vectors.recency_factors(dates, 30)
    assert factors["new.md"] == pytest.approx(1.0)
    assert factors["half.md"] == pytest.approx(0.5, abs=0.01)
    # 2026-06-26 -> 2026-09-25 is 91 days, just over three half-lives.
    assert factors["old.md"] == pytest.approx(0.5 ** (91 / 30), abs=0.001)


def test_recency_factors_treat_an_unknown_date_as_neutral():
    factors = vectors.recency_factors({"a.md": "2026-09-25", "b.md": ""}, 30)
    assert factors["a.md"] == 1.0
    assert factors["b.md"] == vectors._UNKNOWN_RECENCY
    # A corpus with no dates at all must not be reordered or crash.
    assert vectors.recency_factors({"b.md": "not-a-date"}, 30) == {
        "b.md": vectors._UNKNOWN_RECENCY}


def test_zero_half_life_disables_decay():
    """A half-life of 0 means "stop caring about age", not "everything is old"."""
    dates = {"new.md": "2026-09-25", "ancient.md": "2020-01-01"}
    assert vectors.recency_factors(dates, 0) == {"new.md": 1.0, "ancient.md": 1.0}


def test_newer_report_wins_when_relevance_is_equal(db):
    """The bug this fixes: asking about today got answered with June reports."""
    _index_on("stale.md", "2026-06-01", "Widget report", "widget shipped today")
    _index_on("fresh.md", "2026-09-25", "Widget report", "widget shipped today")

    plain = vectors.hybrid_search("widget", None, limit=5)
    assert {h["path"] for h in plain} == {"output/2026-06-01/stale.md",
                                           "output/2026-09-25/fresh.md"}

    weighted = vectors.hybrid_search("widget", None, limit=5, recency_weight=0.5,
                                     half_life_days=30)
    assert weighted[0]["path"] == "output/2026-09-25/fresh.md"
    assert weighted[0]["report_date"] == "2026-09-25"
    # Relevance and recency stay separately visible, so a surprising order can
    # be explained instead of guessed at.
    assert weighted[0]["relevance"] > 0
    assert weighted[0]["recency"] == pytest.approx(1.0)


def test_relevance_still_dominates_a_much_older_report(db):
    """Recency is a tie-breaker with a budget, not a thumb on the scale.

    BM25 here is length-normalised, so the long fresh report scores 0.55 while
    the short old one scores 1.0 — a gap wider than the weight, and no amount
    of freshness moves it.
    """
    _index_on("strong-old.md", "2026-06-01", "Widget", "widget widget widget")
    _index_on("long-fresh.md", "2026-09-25", "Widget", "widget " + "filler " * 60)

    hits = vectors.hybrid_search("widget", None, limit=5, recency_weight=0.25,
                                 half_life_days=30)
    assert hits[0]["path"] == "output/2026-06-01/strong-old.md"
    assert hits[0]["relevance"] == pytest.approx(1.0)
    assert hits[0]["recency"] < 0.2


def test_zero_weight_keeps_pure_relevance_and_still_reports_dates(db):
    _index_on("stale.md", "2026-06-01", "Widget report", "widget shipped today")
    _index_on("fresh.md", "2026-09-25", "Widget report", "widget shipped today")

    hits = vectors.hybrid_search("widget", None, limit=5, recency_weight=0)
    # Equal relevance, and the order is left exactly as retrieval returned it.
    assert [h["relevance"] for h in hits] == [1.0, 1.0]
    assert "recency" not in hits[0]
    assert [h["path"] for h in hits] == ["output/2026-06-01/stale.md",
                                         "output/2026-09-25/fresh.md"]
    # The date is still exposed, so a citation can always show it.
    assert hits[0]["report_date"] == "2026-06-01"


def test_overfetch_lets_a_fresher_report_be_recalled(db):
    """Recency cannot promote a report that recall never returned.

    The eight June reports and the September one are equally relevant, so a
    six-deep pool is entirely June by row order. Overfetching pulls the
    September report in, and only then can its date put it first.
    """
    for i in range(1, 9):
        _index_on(f"old{i:02d}.md", f"2026-06-{i:02d}", "Widget",
                  "widget widget widget filler")
    _index_on("fresh.md", "2026-09-25", "Widget", "widget widget widget filler")

    shallow = vectors.hybrid_search("widget", None, limit=6, overfetch=1)
    deep = vectors.hybrid_search("widget", None, limit=6, overfetch=20,
                                 recency_weight=0.25, half_life_days=30)

    assert [h["path"] for h in shallow] == [f"output/2026-06-{i:02d}/old{i:02d}.md"
                                            for i in range(1, 7)]
    assert deep[0]["path"] == "output/2026-09-25/fresh.md"
    assert deep[0]["relevance"] == pytest.approx(1.0)
    # Overfetch widens the candidate pool, not the answer: both still return
    # exactly `limit` hits.
    assert len(deep) == len(shallow) == 6
