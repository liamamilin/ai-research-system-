"""Q&A has to be able to cite the passage that answers the question.

It could not. ``reports_fts`` holds one row per report, so its snippet is a
window around the document's *first* match -- and an intelligence report opens
with a masthead restating its own title, inputs and date window, which is
exactly what a question naming the stage matches. Meanwhile the chunk hits were
merged by report path, so the chunk index was discarded and one entry per
report survived.

Asked which opportunities P7 ranked P0, the retriever returned four dated
copies of that masthead and the model said, correctly, twice, that the material
was insufficient.
"""

from __future__ import annotations

import pytest

from web.indexer import vectors


MASTHEAD = (
    "# 内容与产品机会（Product and Content Opportunities）\n"
    "**分析窗口**：2026-09-21 → 2026-09-28\n"
    "**输入来源**：本轮上游六份雷达\n"
)
BODY = (
    "| 建议标题 | 优先级 | 转换潜力 |\n|---|---|---|\n"
    "| 编码工具套餐横评 | P0 | 高 |\n"
)


def _hit(path, snippet, score=1.0, chunk=None):
    """A chunk hit, shaped like semantic_search's real return value."""
    return {
        "path": path, "chunk_index": chunk, "snippet": snippet,
        "score": score, "source": "vector",
    }


def _series(day, filename="07_product_content_opportunities.md"):
    return f"practical_ai_intelligence/{day}/{filename}"


def test_a_report_may_not_monopolise_the_context(monkeypatch):
    """One report series is written fresh every round, under the same filename.

    Without a cap the newest few rounds' mastheads fill every slot, because they
    are the highest-scoring lexical match for any question naming the stage. The
    observed failure was four of six slots.
    """
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(_series("2026-09-26"), "masthead 26", 0.9),
        _hit(_series("2026-09-25"), "masthead 25", 0.89),
        _hit(_series("2026-09-24"), "masthead 24", 0.88),
        _hit(_series("2026-09-23"), "masthead 23", 0.87),
        _hit(_series("2026-09-22"), "masthead 22", 0.86),
        _hit("other/plan.md", "the actual finding", 0.5, chunk=7),
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=4)
    per_series = sum(1 for h in hits
                     if h["path"].endswith("07_product_content_opportunities.md"))
    assert per_series <= 2, [h["path"] for h in hits]
    assert any(h["snippet"] == "the actual finding" for h in hits), \
        "the real passage was crowded out by copies of a masthead"
    # Fewer than `limit` is the correct outcome here: five of six candidates
    # were the same series, and padding back to four would mean handing the
    # model two more copies of a masthead it has already been shown.
    assert len(hits) < 4


def test_an_fts_hit_quotes_a_chunk_not_a_document_window(monkeypatch):
    """The FTS snippet is document-level and lands in the masthead.

    Now a report matched by keyword borrows the best-matching chunk of its own
    text, which is the passage that actually matched.
    """
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [{
        "path": _series("2026-09-28"), "title": "P7",
        "snippet": MASTHEAD, "fts_score": 9.0,
    }])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(_series("2026-09-28"), BODY, 0.6, chunk=3),
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=5)
    assert hits, "nothing came back"
    assert hits[0]["snippet"] == BODY
    assert hits[0]["chunk_index"] == 3
    assert hits[0]["source"] == "hybrid"


def test_a_report_with_no_matching_chunk_keeps_its_fts_snippet(monkeypatch):
    """Better a coarse window than nothing at all."""
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [{
        "path": "research/x.md", "title": "X", "snippet": "some text", "fts_score": 5.0,
    }])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=5)
    assert [h["snippet"] for h in hits] == ["some text"]


def test_chunks_from_several_rounds_survive_as_separate_candidates(monkeypatch):
    """Chunk identity is part of the key now, so a second passage in the same
    report is reachable instead of being collapsed into the first."""
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [{
        "path": _series("2026-09-28"), "title": "P7",
        "snippet": MASTHEAD, "fts_score": 9.0,
    }])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(_series("2026-09-28"), BODY, 0.8, chunk=3),
        _hit(_series("2026-09-28"), "a second passage", 0.7, chunk=9),
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=4)
    assert {h.get("chunk_index") for h in hits} >= {3, 9}


def test_the_cap_counts_by_filename_across_rounds():
    # The whole point: the same stage is a different path every round but the
    # same filename, and grouping has to survive the date.
    assert vectors._source_name(
        "practical_ai_intelligence/2026-09-26/07_product_content_opportunities.md"
    ) == vectors._source_name(
        "practical_ai_intelligence/2026-01-02/07_product_content_opportunities.md"
    )
    assert vectors._source_name("a/b.md") != vectors._source_name("a/c.md")


def test_capping_can_be_turned_off():
    hits = [{"path": f"x/{n}.md", "snippet": n} for n in range(5)]
    assert len(vectors._cap_per_source(hits, limit=5, per_source=0)) == 5
    assert len(vectors._cap_per_source(hits, limit=5, per_source=2)) == 5
    assert len(vectors._cap_per_source(hits, limit=2, per_source=2)) == 2
