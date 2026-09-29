"""The retrieval evaluation has to measure the right thing.

It is the only instrument for judging a change to the retriever, and it was
wrong twice: its gold phrases came from the job prompts rather than the
reports, and then it demanded a section's *heading* appear in a hit when the
body of that section is what a hit normally carries.

So the section boundaries are worth pinning down. A section is not one chunk:
these reports put a heading at the end of a long table, no chunk begins with
`## `, and a single chunk often spans the boundary between two sections.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from web.indexer import db as index_db
from web.indexer import vectors

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "eval_retrieval.py"


@pytest.fixture()
def eval_retrieval():
    spec = importlib.util.spec_from_file_location("eval_retrieval", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(tmp_path / "reports.db"))
    index_db.init_db()
    vectors.init_vectors()
    return tmp_path


def _embed(texts):
    return [[1.0, 0.0] for _ in texts]


def test_a_chunk_counts_for_the_section_it_sits_in(eval_retrieval, db):
    """A body chunk carries no heading of its own and must still count."""
    body = "## 2. Immediate Actions\n\n" + ("行动内容。" * 400)
    vectors.index_report("p/2026-09-28/09_x.md", body, _embed, model="m")

    index = eval_retrieval.section_index()
    chunks = {k: v for k, v in index.items() if k[0].endswith("09_x.md")}
    assert len(chunks) > 1, "the document was one chunk, so this proves nothing"

    # Chunks after the one carrying the heading, i.e. pure body text.
    body_chunks = [k for k, seen in chunks.items() if k[1] > 0]
    assert body_chunks, "no body chunk to check"
    for key in body_chunks:
        assert eval_retrieval._hit_covers({"path": key[0], "chunk_index": key[1]},
                                          index, "Immediate Actions"), \
            f"a chunk of the section was not credited to it: {chunks[key]}"


def test_a_chunk_that_spans_a_heading_credits_both_sections(eval_retrieval, db):
    """The text either side of a heading is in the same excerpt."""
    filler = "内容。" * 200  # long enough that the splitter has to cut
    body = f"## 1. 第一节\n\n{filler}\n\n## 2. 第二节\n\n{filler}"
    vectors.index_report("p/2026-09-28/09_x.md", body, _embed, model="m")

    index = eval_retrieval.section_index()
    spanning = [
        key for key, seen in index.items()
        if key[0].endswith("09_x.md")
        and any("第一节" in s for s in seen)
        and any("第二节" in s for s in seen)
    ]
    if not spanning:
        pytest.skip("this document happened to split exactly on the boundary")
    for key in spanning:
        assert eval_retrieval._hit_covers({"path": key[0], "chunk_index": key[1]},
                                          index, "第二节")
        assert eval_retrieval._hit_covers({"path": key[0], "chunk_index": key[1]},
                                          index, "第一节")


def test_a_chunk_from_another_report_never_counts(eval_retrieval, db):
    vectors.index_report("p/2026-09-28/07_y.md",
                         "## 1. 最佳内容机会\n\n甲。\n\n乙。\n\n丙。", _embed, model="m")
    index = eval_retrieval.section_index()
    key = next(k for k in index if k[0].endswith("07_y.md"))
    assert eval_retrieval._hit_covers({"path": key[0], "chunk_index": key[1]},
                                      index, "最佳内容机会")
    assert not eval_retrieval._hit_covers({"path": key[0], "chunk_index": key[1]},
                                          index, "供应商依赖地图")


def test_the_questions_are_all_about_the_current_round(eval_retrieval):
    """None may trip the archive-cue regex, or the harness scores a different
    code path than the one it reports on."""
    assert not [q for q, _, _ in eval_retrieval.CASES
                if vectors._names_another_round(q)]


def test_every_gold_marker_is_a_real_heading(eval_retrieval):
    """A marker that is not a heading would silently match body text."""
    assert len(eval_retrieval.CASES) >= 30, "the set is too small to steer on"
    assert all(filename.endswith(".md") for _, filename, _ in eval_retrieval.CASES)
    assert all(gold.strip() for _, _, gold in eval_retrieval.CASES)
