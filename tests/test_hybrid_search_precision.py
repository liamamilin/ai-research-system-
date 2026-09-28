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


def test_every_chunk_reaches_ranking_not_just_the_first_two_of_a_report(monkeypatch):
    """A report contributed at most two candidates while the pool was built.

    Chunk hits arrive in cosine order and every chunk of a report shares its
    date, so the cap was inert for ranking -- the passages it dropped were
    already below their own report's first two. It was still wrong, because it
    decided what could be ranked at all, and the only reason it was invisible is
    the per-source cap downstream. Dropping it means the shortlist is chosen
    once, on the ranked list, and `max_chunks_per_report` means one thing.

    With the cap disabled, all five chunks of one report come back: a cap
    applied during construction would return none of them.
    """
    report = _series("2026-09-28")
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(report, f"chunk {i}", 0.9 - i / 100, chunk=i) for i in range(5)
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=5, max_chunks_per_report=0)
    assert len(hits) == 5, [h["snippet"] for h in hits]


def test_the_cap_still_shrinks_the_answer(monkeypatch):
    """The cap is a property of the answer, so it still applies at the end."""
    report = _series("2026-09-28")
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(report, f"chunk {i}", 0.9 - i / 100, chunk=i) for i in range(5)
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=5)
    assert len(hits) == 2, [h["snippet"] for h in hits]


def test_the_cap_still_shrinks_the_answer(monkeypatch):
    """The cap is a property of the answer, so it still applies at the end."""
    report = _series("2026-09-28")
    monkeypatch.setattr(vectors.index_db, "search_reports", lambda q, n: [])
    monkeypatch.setattr(vectors, "semantic_search", lambda v, n: [
        _hit(report, f"chunk {i}", 0.9 - i / 100, chunk=i) for i in range(5)
    ])
    monkeypatch.setattr(vectors, "report_dates", lambda paths: {})

    hits = vectors.hybrid_search("q", [0.1], limit=5)
    assert len(hits) <= 2, [h["snippet"] for h in hits]


def test_a_heading_is_never_the_last_thing_in_a_chunk():
    """Filling greedily to `size` could end a chunk on its heading, stranding
    the section body in the next chunk. The model then received an excerpt
    stopping at "## 2. Immediate Actions" and reported the text as truncated.
    """
    doc = ("前言段落。" * 20) + "\n\n## 2. Immediate Actions\n\n" + ("行动内容。" * 10)
    for chunk in vectors.chunk_text(doc, size=200):
        assert not chunk.rstrip().split("\n")[-1].startswith("# "), \
            f"a chunk ends on a heading: {chunk[-40:]!r}"


def test_a_heading_is_never_left_alone_either():
    """The other half of the rule, and getting it wrong is worse.

    These reports put a table of several thousand characters under each
    heading, so a naive "start a chunk at every heading" produced bare
    12-character titles. Four of the six hits for a question about model
    routing were exactly that, and answerability fell from 12 of 15 to 7.
    """
    doc = ("前言段落。" * 20) + "\n\n## 2. Immediate Actions\n\n" + ("行动内容。" * 60)
    for chunk in vectors.chunk_text(doc, size=200):
        assert not (chunk.startswith("# ") and "\n\n" not in chunk), \
            f"a chunk is a bare heading: {chunk!r}"


def test_a_heading_travels_with_the_text_it_introduces():
    doc = ("前言段落。" * 20) + "\n\n## 2. Immediate Actions\n\n" + ("行动内容。" * 10)
    chunks = vectors.chunk_text(doc, size=200)
    holder = [c for c in chunks if "## 2. Immediate Actions" in c]
    assert holder, "the heading was dropped"
    assert "行动内容" in holder[0], "the body was left in another chunk"


def test_short_sections_are_not_split_at_all():
    """Two short sections are small enough to share a chunk, and that is fine.

    An earlier draft forced every heading to open a chunk, on the theory that a
    section is better retrieved in isolation. Measured on the real reports that
    produced bare 12-character heading chunks, so the rule was dropped; the
    guarantee kept is the negative one, that a chunk never *ends* on a heading.
    """
    chunks = vectors.chunk_text("## A\n\n正文甲。\n\n## B\n\n正文乙。", size=200)
    assert len(chunks) == 1
    assert chunks[0] == "## A\n\n正文甲。\n\n## B\n\n正文乙。"


def test_chunking_still_covers_every_byte_of_the_document():
    """The heading rules move text between chunks; nothing may be lost."""
    doc = ("前言段落。" * 30) + "\n\n## 一节\n\n" + ("内容甲。" * 40) + \
        "\n\n## 二节\n\n" + ("内容乙。" * 40)
    chunks = vectors.chunk_text(doc)
    assert "".join(chunks).replace("\n\n", "") == doc.replace("\n\n", ""), \
        "chunking lost or duplicated text"
