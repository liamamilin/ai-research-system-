"""The synthesis stages receive their upstream documents, and bad output fails.

Both features exist because the same thing kept happening: P7/P8/P9 were asked
to read documents they were never given, so the model went looking for them with
read_file -- burning its context budget, sometimes finding the wrong date's file,
and then being flagged for "hallucinating" the very URLs it had legitimately
read.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path

import pytest

from core.research import report_problem
from web.runner.pipeline import STAGE_DEPS, collect_upstream


@pytest.fixture()
def round_dir(tmp_path):
    """A round directory with one document per stage."""
    root = tmp_path / "2026-01-02"
    root.mkdir()
    from web.runner.pipeline import STAGES
    for key, filename, _label in STAGES:
        (root / filename).write_text(
            f"# {key}\n\n" + f"内容 {key}。" * 400, encoding="utf-8")
    return str(root)


# --- which 60k of a 32k radar the synthesis stage actually gets ---------------

def _radar(sections: dict[str, int]) -> str:
    """A radar-shaped document: preamble plus top-level sections of given sizes."""
    parts = ["# 雷达\n\n前言。\n"]
    for heading, size in sections.items():
        parts.append(f"\n## {heading}\n\n" + ("内容" * (size // 2)))
    return "\n".join(parts)


def test_the_tail_of_a_radar_survives_the_budget():
    """The sections a synthesis stage is told to use live at the end.

    P7's prompt asks for "Plan comparisons", "Tool recommendations" and
    "Ignore / Not Worth Content"; P1's radar answers those in sections 2, 3
    and 7. Truncating at the head handed P7 the opening nine items and none of
    them, which is the worst possible trade for a 32k document and a 10k share.
    """
    from web.runner.pipeline import _select_sections

    text = _radar({
        "1. 高信号更新": 18000,
        "2. 对比表": 6000,
        "3. 详细分析": 5000,
        "4. 定价说明": 2000,
        "5. 路由影响": 1500,
        "6. Test Candidates": 1200,
        "7. Ignore / Low-Signal Items": 800,
    })
    out = _select_sections(text, 10000, path="radar.md")

    assert len(out) <= 10400, f"over budget: {len(out)}"
    assert "## 7. Ignore / Low-Signal Items" in out
    assert "## 6. Test Candidates" in out
    assert "前言" in out
    # And it must say what it dropped, with a route to the original.
    assert "省略了约" in out
    assert "radar.md" in out


def test_the_result_never_exceeds_the_share():
    """The preamble and the omission note are emitted outside the section
    budget, so they have to be subtracted from it. P7 came out at 10,313
    against a 10,000 share before this was accounted for."""
    from web.runner.pipeline import _select_sections

    text = _radar({"1. 开篇": 26000, "2. 中段": 6000, "3. 收尾": 3000})
    for limit in (4000, 6000, 10000):
        out = _select_sections(text, limit, path="radar.md")
        assert len(out) <= limit, f"limit {limit} produced {len(out)}"


def test_a_short_enough_document_is_handed_over_whole():
    from web.runner.pipeline import _select_sections

    text = _radar({"1. 更新": 2000, "2. 对比表": 1500})
    out = _select_sections(text, 10000, path="radar.md")
    assert out == text
    assert "省略" not in out


def test_an_oversized_first_section_is_clipped_not_dropped():
    """One huge opening section must not starve the whole budget.

    The first cut of this walked sections and stopped at the first one too big
    to fit, which on the real P1 produced 4,036 characters out of a 10,000
    budget -- a third of what head-truncation had managed, with the framing
    gone too.
    """
    from web.runner.pipeline import _select_sections

    text = _radar({"1. 巨大的开篇": 30000, "2. 中段": 6000, "3. 收尾": 3000})
    out = _select_sections(text, 10000, path="radar.md")

    assert "## 1. 巨大的开篇" in out, "the opening section was dropped"
    assert "## 3. 收尾" in out, "the tail was dropped"
    # Not 10,000: the 6k middle section cannot fit the ~900 characters left
    # after the opening is clipped, and half a section is worse than none.
    assert len(out) > 8500, f"only {len(out)} of 10000 used"
    assert len(out) <= 10400, f"over budget: {len(out)}"


def test_a_two_section_document_keeps_both_even_if_the_budget_is_not_full():
    """Two sections is the floor: once the first is clipped there is nothing
    left to take, so the share is not filled. Documented rather than papered
    over -- an unfilled budget is better than a cut section."""
    from web.runner.pipeline import _select_sections

    text = _radar({"1. 巨大的开篇": 30000, "2. 收尾": 2000})
    out = _select_sections(text, 10000, path="radar.md")

    assert "## 1. 巨大的开篇" in out
    assert "## 2. 收尾" in out
    assert len(out) > 6000


def test_selection_keeps_sections_in_reading_order():
    from web.runner.pipeline import _select_sections

    text = _radar({"1. 甲": 4000, "2. 乙": 4000, "3. 丙": 4000, "4. 丁": 4000})
    out = _select_sections(text, 10000, path="radar.md")
    headings = [line for line in out.splitlines() if line.startswith("## ")]
    assert headings, "nothing was kept"
    numbers = [int(h[3]) for h in headings]
    assert numbers == sorted(numbers), f"out of order: {headings}"


def test_an_unstructured_document_keeps_both_ends():
    from web.runner.pipeline import _select_sections

    text = ("开头标记。" + "填充" * 6000 + "结尾标记。")
    out = _select_sections(text, 4000, path="plain.md")
    assert "开头标记" in out
    assert "结尾标记" in out
    assert "省略了约" in out
    assert len(out) <= 4400


def test_the_budget_is_never_silently_spent_on_a_prefix():
    """The share is 60k/6; P1 is 32k. Whatever is dropped, most of the share
    has to be filled -- otherwise the stage is handed a fragment and told it
    received the full document."""
    from web.runner.pipeline import _select_sections

    root = Path(os.path.dirname(os.path.abspath(__file__))).parent
    real = root / "output" / "practical_ai_intelligence"
    versions = sorted(p for p in real.glob("2026-*") if p.is_dir()) if real.is_dir() else []
    if not versions:
        pytest.skip("no round output on this machine")
    radar = versions[-1] / "01_model_and_pricing_radar.md"
    if not radar.is_file():
        pytest.skip("this round has no P1")

    original = radar.read_text(encoding="utf-8")
    if len(original) < 15000:
        pytest.skip("radar too small to be truncated")

    share = 60000 // 6
    out = _select_sections(original, share, path="p1.md")
    assert len(out) > share * 0.85, f"only used {len(out)} of {share}"


# --- upstream collection -----------------------------------------------------

def test_p9_receives_p7_and_p8(round_dir):
    text, notes = collect_upstream("09_executive_synthesis_and_actions", round_dir)
    assert text, "the final synthesis must receive its inputs"
    assert "07_product_content_opportunities.md" in text
    assert "08_risk_and_alternatives.md" in text
    assert notes == []


def test_p7_receives_the_radars(round_dir):
    text, _notes = collect_upstream("07_product_content_opportunities", round_dir)
    assert "01_model_and_pricing_radar.md" in text
    assert "06_infra_and_eval_radar.md" in text
    # P7 must not be handed its own downstream.
    assert "09_executive_synthesis_and_actions.md" not in text


def test_an_independent_radar_gets_nothing(round_dir):
    """P1-P6 have no upstream; injecting something would be misleading."""
    text, notes = collect_upstream("01_model_and_pricing_radar", round_dir)
    assert text == ""
    assert notes == []


def test_collection_follows_the_declared_dependencies(round_dir):
    for key, deps in STAGE_DEPS.items():
        text, _ = collect_upstream(key, round_dir)
        if not deps:
            assert text == "", key
            continue
        for dep in deps:
            from web.runner.pipeline import _STAGE_FILE
            assert _STAGE_FILE[dep] in text, f"{key} is missing {dep}"


def test_a_missing_upstream_document_is_stated_not_hidden(tmp_path):
    """Silence would let the model invent the missing section."""
    empty = tmp_path / "round"
    empty.mkdir()
    text, notes = collect_upstream("07_product_content_opportunities", str(empty))
    assert text == ""
    assert notes, "a missing input must be reported"
    assert "缺失" in notes[0]


def test_a_stub_document_is_ignored_with_a_reason(tmp_path):
    """A 10-character file is not a radar; treating it as one invents findings."""
    root = tmp_path / "round"
    root.mkdir()
    from web.runner.pipeline import STAGES, _STAGE_FILE
    for key, filename, _label in STAGES:
        body = "# x\n\nok" if key.startswith("01_") else f"# {key}\n\n" + "内容。" * 500
        (root / filename).write_text(body, encoding="utf-8")
    text, notes = collect_upstream("07_product_content_opportunities", str(root))
    assert "过短" in " ".join(notes)
    assert _STAGE_FILE["01_model_and_pricing_radar"] not in text


def test_injection_respects_a_length_budget(tmp_path):
    """Six radars run to ~150k chars, which is the whole context budget."""
    from web.runner.pipeline import STAGES, UPSTREAM_CHAR_BUDGET, _STAGE_FILE
    root = tmp_path / "round"
    root.mkdir()
    for key, filename, _label in STAGES:
        (root / filename).write_text(
            f"# {key}\n\n" + "x" * 40000, encoding="utf-8")
    text, _notes = collect_upstream("07_product_content_opportunities", str(root))
    assert len(text) <= UPSTREAM_CHAR_BUDGET + 5000
    # Truncation is stated where the reader hits it, not only in a summary.
    assert "省略了约" in text
    # And the budget is shared, so no radar is dropped entirely.
    for key in STAGES[1:7]:
        assert _STAGE_FILE[key[0]] in text, key[0]


def test_truncation_is_declared_inside_the_text(tmp_path):
    from web.runner.pipeline import STAGES
    root = tmp_path / "round"
    root.mkdir()
    for key, filename, _label in STAGES:
        (root / filename).write_text(f"# {key}\n\n" + "y" * 50000, encoding="utf-8")
    text, _ = collect_upstream("07_product_content_opportunities", str(root))
    assert "省略了约" in text


# --- the prompt actually receives it -----------------------------------------

def test_prompt_renders_upstream_into_the_job_template(round_dir):
    from core.config import load_job
    from core.engine import ResearchEngine

    upstream, _ = collect_upstream("07_product_content_opportunities", round_dir)
    assert upstream
    engine = ResearchEngine(config_dir="config", jobs_dir="jobs",
                            workspace_dir=".", prompt_vars={"upstream_reports": upstream})
    job = load_job("jobs", "practical_ai_intelligence/07_product_content_opportunities")
    prompt = engine._build_prompt(job)
    assert "{upstream_reports}" not in prompt, "placeholder was not substituted"
    assert "01_model_and_pricing_radar.md" in prompt


def test_caller_vars_override_built_in_ones():
    from core.engine import ResearchEngine
    engine = ResearchEngine(config_dir="config", jobs_dir="jobs", workspace_dir=".",
                            prompt_vars={"date": "1999-01-01"})
    assert "{date}" not in engine._build_prompt({"prompt": "on {date}", "name": "x"})


def test_the_three_synthesis_jobs_declare_the_placeholder():
    """Otherwise the injection is collected and then thrown away."""
    from core.config import load_job
    for name in ("07_product_content_opportunities",
                 "08_risk_and_alternatives",
                 "09_executive_synthesis_and_actions"):
        job = load_job("jobs", f"practical_ai_intelligence/{name}")
        assert "{upstream_reports}" in job["prompt"], name


# --- citations inherited from upstream are not fabrications -------------------

def test_urls_read_from_a_file_count_as_traceable(tmp_path):
    """A synthesis stage citing URLs it read is not hallucinating them."""
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.retrieved_urls = set()
    agent._remember_urls_in(
        "见 https://openai.com/index/x 和 http://blog.y.com/z。\n"
        "参考 https://openai.com/index/x，重复引用。\n"
    )
    assert agent.retrieved_urls == {
        "https://openai.com/index/x", "http://blog.y.com/z"}


def test_trailing_punctuation_is_stripped_from_remembered_urls():
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.retrieved_urls = set()
    agent._remember_urls_in("来源：(https://a.example/b)。")
    assert agent.retrieved_urls == {"https://a.example/b"}


def test_urls_inside_a_search_snippet_count_as_traceable():
    """A link inside a result's excerpt is something the model was shown.

    format_results() renders the excerpts as well as the links, and a news
    snippet routinely carries an outbound URL. Recording only each result's own
    url reported every one of those as fabricated -- the 2026-09-26 research/ai
    run was flagged at 53% coverage, and the 42 "unmatched" URLs were real
    sources the model had read.
    """
    from core.research import ResearchAgent
    from core.search import SearchResult

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.retrieved_urls = set()
    agent.config = SimpleNamespace(max_searches=5, max_chars_per_call=100_000)
    agent._search_calls = 0
    agent._progress = lambda _e: None
    agent.search = SimpleNamespace(
        search=lambda queries, objective="": [
            SearchResult(
                title="Aggregator roundup",
                url="https://news.example/roundup",
                excerpts=["详见 https://www.infoworld.com/article/4221163/x.html"],
            )
        ]
    )

    rendered = agent._tool_search_web({"queries": ["ai agents"]})
    assert "infoworld.com/article/4221163" in rendered, "the snippet should reach the model"
    assert "https://www.infoworld.com/article/4221163/x.html" in agent.retrieved_urls
    assert "https://news.example/roundup" in agent.retrieved_urls


def test_a_url_only_in_a_truncated_snippet_is_not_credited(monkeypatch):
    """The model never saw past the truncation, so neither did the run."""
    from core.research import ResearchAgent
    from core.search import SearchResult

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.retrieved_urls = set()
    # A limit small enough to cut the excerpt off entirely.
    agent.config = SimpleNamespace(max_searches=5, max_chars_per_call=60)
    agent._search_calls = 0
    agent._progress = lambda _e: None
    agent.search = SimpleNamespace(
        search=lambda queries, objective="": [
            SearchResult(
                title="T",
                url="https://news.example/a",
                excerpts=["x" * 200 + " https://cut.example/b"],
            )
        ]
    )

    rendered = agent._tool_search_web({"queries": ["q"]})
    assert "cut.example" not in rendered
    assert "https://cut.example/b" not in agent.retrieved_urls


# --- output validation -------------------------------------------------------

def test_a_real_report_is_accepted():
    body = "# 模型定价\n\n" + "内容 https://x.com " * 60
    assert report_problem(body, tools_used=5, min_chars=400) is None


@pytest.mark.parametrize("body,expected", [
    ("", "内容为空"),
    ("   \n ", "内容为空"),
    ("I'm sorry, I cannot browse the web.", "拒绝"),
    ("抱歉，我无法完成这个任务。", "拒绝"),
    ("Error: rate limit exceeded (http 429)", "错误"),
    ("Invalid API key provided", "错误"),
    ("upstream connect error or disconnect", "错误"),
])
def test_unusable_replies_are_rejected(body, expected):
    problem = report_problem(body, tools_used=0, min_chars=400)
    assert problem and expected in problem


def test_a_short_reply_without_research_is_rejected():
    assert "不足" in (report_problem("好的", tools_used=0, min_chars=400) or "")


def test_a_long_reply_with_no_structure_is_rejected():
    assert "标题" in (report_problem("x" * 900, tools_used=3, min_chars=400) or "")


def test_a_leaked_system_prompt_is_rejected():
    leak = "You are a helpful assistant. Always cite sources. " * 20
    assert report_problem(leak, tools_used=0, min_chars=400) is not None


def test_validation_is_conservative_about_real_content():
    """A false positive costs a whole paid run, so err toward accepting."""
    terse = "## 结论\n\nAPI 定价下降，来源 https://a.example" + " 细节。" * 120
    assert len(terse) > 400
    assert report_problem(terse, tools_used=4, min_chars=400) is None
