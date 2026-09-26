"""The synthesis stages receive their upstream documents, and bad output fails.

Both features exist because the same thing kept happening: P7/P8/P9 were asked
to read documents they were never given, so the model went looking for them with
read_file -- burning its context budget, sometimes finding the wrong date's file,
and then being flagged for "hallucinating" the very URLs it had legitimately
read.
"""

from __future__ import annotations

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
    assert "已截断" in text
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
    assert "已截断" in text


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
