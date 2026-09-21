"""Tests for usage aggregation, report metadata and budget guardrails."""

from __future__ import annotations

import json

from core import budget, report_meta
from core.state import StateManager

from web.runner import pipeline


# ---------------------------------------------------------------------------
# Usage summary (state/history)
# ---------------------------------------------------------------------------


def test_usage_summary_aggregates(tmp_path, monkeypatch):
    history = tmp_path / "history"
    history.mkdir()
    entries = [
        {"ts": "2026-01-01T06:00:00+0800", "job_name": "job/a", "status": "success",
         "usage": {"model": "m1", "prompt_tokens": 100, "completion_tokens": 50,
                   "total_tokens": 150, "searches": 2}},
        {"ts": "2026-01-02T06:00:00+0800", "job_name": "job/b", "status": "success",
         "usage": {"model": "m1", "prompt_tokens": 10, "completion_tokens": 5,
                   "total_tokens": 15, "searches": 1}},
        {"ts": "2026-01-02T06:05:00+0800", "job_name": "job/b", "status": "failed"},
    ]
    with open(history / "job_a.jsonl", "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    import core.state as state_mod
    monkeypatch.setattr(state_mod, "_HISTORY_DIR", str(history))

    summary = StateManager.get_usage_summary(days=0)
    assert summary["totals"]["runs"] == 3
    assert summary["totals"]["runs_with_usage"] == 2
    assert summary["totals"]["total_tokens"] == 165
    assert summary["totals"]["searches"] == 3
    assert summary["per_model"][0]["model"] == "m1"


# ---------------------------------------------------------------------------
# Report metadata
# ---------------------------------------------------------------------------


def test_report_meta_round_tokens(tmp_path, monkeypatch):
    meta_file = tmp_path / "report_meta.jsonl"
    monkeypatch.setattr(report_meta, "META_DIR", str(tmp_path))
    monkeypatch.setattr(report_meta, "META_FILE", str(meta_file))
    report_meta._cache.update({"mtime": 0.0, "map": {}})

    report_meta.append_record(
        "output/practical_ai_intelligence/2026-01-02/01_x.md",
        "practical_ai_intelligence/01_model_and_pricing_radar",
        usage={"model": "m", "total_tokens": 1000, "searches": 3},
        duration_seconds=60,
    )
    records = report_meta.load_all()
    record = records["practical_ai_intelligence/2026-01-02/01_x.md"]
    assert record["round_date"] == "2026-01-02"
    assert report_meta.round_tokens("2026-01-02") == {
        "01_model_and_pricing_radar": 1000
    }


def test_normalize_rel():
    assert report_meta.normalize_rel("output/a/b.md") == "a/b.md"
    assert report_meta.normalize_rel("/a/b.md") == "a/b.md"
    assert report_meta.normalize_rel("a/b.md") == "a/b.md"


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_estimate_cost():
    totals = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000}
    assert budget._estimate_cost(totals, {"input_per_1m": 0.15,
                                          "output_per_1m": 0.60}) == 0.45
    assert budget._estimate_cost(totals, {}) == 0.0


def test_month_spend_and_block(monkeypatch):
    monkeypatch.setattr(
        StateManager, "get_usage_summary",
        staticmethod(lambda days: {"totals": {"prompt_tokens": 10_000_000,
                                              "completion_tokens": 0,
                                              "total_tokens": 10_000_000}}),
    )
    cfg = {
        "ai": {"pricing": {"input_per_1m": 1.0, "output_per_1m": 1.0}},
        "budget": {"monthly_usd_limit": 5, "warn_ratio": 0.8,
                   "block_pipeline": True},
    }
    status = budget.month_spend(cfg)
    assert status["spent"] == 10.0
    assert status["exceeded"] is True

    allowed, reason = budget.pipeline_allowed(cfg)
    assert allowed is False and "预算" in reason

    cfg["budget"]["block_pipeline"] = False
    allowed, _ = budget.pipeline_allowed(cfg)
    assert allowed is True


def test_pipeline_guard_wired(monkeypatch):
    """start_round must refuse when the budget is exceeded."""
    monkeypatch.setattr(pipeline, "pipeline_allowed",
                        lambda: (False, "budget exceeded for test"))
    try:
        pipeline.start_round("config", "jobs")
        raise AssertionError("round started despite budget")
    except RuntimeError as exc:
        assert "budget exceeded" in str(exc)
