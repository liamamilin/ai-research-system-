"""The daily round must receive its upstream documents too.

The launchd agent runs each stage as a separate ``run.py`` process, so it never
went through the Web runner that was injecting ``{upstream_reports}``. P7, P8
and P9 were sent the literal placeholder plus a paragraph promising documents
that were never attached -- and the synthesis stages, the ones that produce the
product, ran with no input at all. Nothing reported it.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pytest

from core import load_job
from core.pipeline_docs import (
    STAGE_DEPS,
    STAGE_FILE,
    STAGES,
    stage_round_dir,
    upstream_for,
)

P7 = "07_product_content_opportunities"
P9 = "09_executive_synthesis_and_actions"
P1 = "01_model_and_pricing_radar"


def _write_round(root: Path, date: str, size: int = 6000) -> str:
    round_dir = root / "output" / "practical_ai_intelligence" / date
    round_dir.mkdir(parents=True, exist_ok=True)
    for key, filename, _label in STAGES:
        (round_dir / filename).write_text(
            f"# {key}\n\n## 1. 主体\n\n" + "内容" * (size // 2), encoding="utf-8")
    return str(round_dir)


def _job(root: Path, date: str, key: str = P7) -> dict:
    """A job dict shaped like load_job's, writing into the round directory."""
    return {
        "_file": f"practical_ai_intelligence/{key}",
        "output": f"output/practical_ai_intelligence/{date}/{STAGE_FILE[key]}",
    }


class _Engine:
    def __init__(self, date: str, root: Path | str = ".") -> None:
        self._stamp = datetime.strptime(date, "%Y-%m-%d")
        self.workspace_dir = str(root)

    def _now(self) -> datetime:
        return self._stamp


def _upstream_vars(jobs_dir: str, engine, name: str) -> dict:
    """The helper run.py uses, exercised through the real module."""
    from run import _upstream_vars as real

    args = argparse.Namespace(jobs_dir=jobs_dir)
    return real(args, engine, name)


# --- the round directory comes from the stage's own output template -----------

def test_round_dir_is_read_from_the_output_template(tmp_path):
    # Re-deriving the date in the runner is how a backfill ends up reading one
    # round's radars while writing into another's.
    assert stage_round_dir(_job(tmp_path, "2026-01-02"), "2026-01-02") == \
        "output/practical_ai_intelligence/2026-01-02"


def test_a_backfilled_stage_reads_its_own_round(tmp_path):
    _write_round(tmp_path, "2025-12-30")
    job = _job(tmp_path, "2025-12-30")
    text = upstream_for(P7, job, "2025-12-30", root=str(tmp_path))
    assert text, "the backfilled round has radars but produced nothing"
    assert STAGE_FILE["06_infra_and_eval_radar"] in text


def test_a_flat_output_path_is_not_mistaken_for_a_round(tmp_path):
    for template in ("output/{date}_07.md", "output/reports/07.md", ""):
        job = {"_file": "practical_ai_intelligence/07_product_content_opportunities",
               "output": template}
        assert stage_round_dir(job, "2026-01-02") == "", template


# --- what the runner actually hands the prompt -------------------------------

def test_a_synthesis_stage_gets_its_upstream_through_run_py(tmp_path, monkeypatch):
    _write_round(tmp_path, "2026-01-02")
    jobs_dir = tmp_path / "jobs" / "practical_ai_intelligence"
    jobs_dir.mkdir(parents=True)
    for key, filename, _label in STAGES:
        (jobs_dir / f"{key}.yaml").write_text(
            f"name: {key}\noutput: output/practical_ai_intelligence/{{date}}/{filename}\n"
            "prompt: \"材料：\\n\\n{upstream_reports}\\n\\n结束。\"\n",
            encoding="utf-8")

    engine = _Engine("2026-01-02", root=tmp_path)
    variables = _upstream_vars(str(tmp_path / "jobs"), engine, P7)

    assert variables, "the daily path handed the stage nothing"
    assert len(variables["upstream_reports"]) > 1000
    # And the placeholder is actually substituted, not sent through literally.
    job = load_job(str(tmp_path / "jobs"), P7)
    rendered = job["prompt"].replace("{upstream_reports}", variables["upstream_reports"])
    assert "{upstream_reports}" not in rendered


def test_a_radar_stage_is_left_alone(tmp_path, monkeypatch):
    _write_round(tmp_path, "2026-01-02")
    jobs_dir = tmp_path / "jobs" / "practical_ai_intelligence"
    jobs_dir.mkdir(parents=True)
    for key, filename, _label in STAGES:
        (jobs_dir / f"{key}.yaml").write_text(
            f"name: {key}\noutput: output/practical_ai_intelligence/{{date}}/{filename}\n"
            "prompt: \"x\"\n", encoding="utf-8")

    engine = _Engine("2026-01-02")
    assert _upstream_vars(str(tmp_path / "jobs"), engine, P1) == {}


def test_an_ordinary_job_gets_no_upstream_variable(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs" / "monitoring"
    jobs_dir.mkdir(parents=True)
    (jobs_dir / "ai.yaml").write_text("name: AI\nprompt: \"x\"\n", encoding="utf-8")

    engine = _Engine("2026-01-02")
    assert _upstream_vars(str(tmp_path / "jobs"), engine, "monitoring/ai") == {}


def test_a_missing_round_is_explained_rather_than_silently_empty(tmp_path):
    # The prompts say the material is attached. Substituting nothing leaves the
    # model reading a claim about a section that is not in its prompt.
    job = _job(tmp_path, "1999-01-01")
    text = upstream_for(P7, job, "1999-01-01", root=str(tmp_path))
    assert "不可用" in text and "1999-01-01" in text
    assert "不要凭空补齐" in text


def test_an_empty_round_directory_is_explained_too(tmp_path):
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / "2026-01-02"
    round_dir.mkdir(parents=True)
    text = upstream_for(P7, _job(tmp_path, "2026-01-02"), "2026-01-02", root=str(tmp_path))
    assert "不可用" in text


# --- the dependency graph has one home ---------------------------------------

def test_both_runners_share_one_dependency_graph():
    from web.runner import pipeline as web_pipeline

    assert web_pipeline.STAGE_DEPS is STAGE_DEPS
    assert web_pipeline.collect_upstream is not None
    assert STAGE_DEPS[P7] and STAGE_DEPS[P9] == (
        "07_product_content_opportunities", "08_risk_and_alternatives")
