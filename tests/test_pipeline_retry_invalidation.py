"""Retry invalidation: re-running a stage must re-run its consumers.

A round that reports success while P7/P8/P9 were synthesised from the *old*
upstream documents is worse than an obvious failure, so a retry now pulls the
transitive downstream stages back in.
"""

from __future__ import annotations

import os

import pytest

from web.runner import pipeline


P1 = "01_model_and_pricing_radar"
P2 = "02_ai_coding_tools_radar"
P7 = "07_product_content_opportunities"
P8 = "08_risk_and_alternatives"
P9 = "09_executive_synthesis_and_actions"
P0 = "00_collection_planner"


def _files(all_exist=True, missing=()):
    return {
        key: {"exists": all_exist and key not in missing, "key": key}
        for key, _f, _l in pipeline.STAGES
    }


def _previous(**statuses):
    return {key: {"status": status} for key, status in statuses.items()}


# --- dependency graph -------------------------------------------------------


def test_dependency_graph_matches_pipeline_structure():
    deps = pipeline.STAGE_DEPS
    assert deps[P0] == ()
    for key, _f, _l in pipeline.STAGES[1:7]:
        assert deps[key] == (), f"{key} should be independent"
    assert set(deps[P7]) == {k for k, _f, _l in pipeline.STAGES[1:7]}
    assert set(deps[P8]) == set(deps[P7])
    assert deps[P9] == (P7, P8)


def test_downstream_of_radar_includes_synthesis_chain():
    down = pipeline.downstream_closure([P1])
    assert down == [P7, P8, P9]


def test_downstream_of_synthesis_is_empty():
    assert pipeline.downstream_closure([P9]) == []


def test_downstream_of_planner_is_empty():
    # P0 is not consumed by the radars today; invalidating them would be waste.
    assert pipeline.downstream_closure([P0]) == []


def test_downstream_closure_is_transitive():
    assert pipeline.downstream_closure([P7]) == [P9]
    # Seeds are never repeated in the result; plan_retry unions the two.
    assert pipeline.downstream_closure([P1, P7]) == [P8, P9]


# --- retry planning ---------------------------------------------------------


def test_plan_includes_downstream_of_failed_stage():
    plan = pipeline.plan_retry(_files(), _previous(**{P1: "failed", P7: "success"}))
    assert plan["needed"] == [P1]
    assert plan["invalidated"] == [P7, P8, P9]
    assert set(plan["run"]) == {P1, P7, P8, P9}


def test_plan_without_invalidation_keeps_old_behavior():
    plan = pipeline.plan_retry(_files(), _previous(**{P1: "failed"}),
                               invalidate_downstream=False)
    assert plan["run"] == [P1]
    assert plan["invalidated"] == []


def test_plan_for_missing_output_cascades():
    plan = pipeline.plan_retry(_files(missing=[P1]), _previous())
    assert P1 in plan["needed"]
    assert set(plan["invalidated"]) == {P7, P8, P9}


def test_plan_ignores_skipped_stages():
    plan = pipeline.plan_retry(_files(), _previous(**{P1: "skipped", P7: "skipped"}))
    assert plan["needed"] == []


def test_plan_treats_stale_as_needing_rerun():
    plan = pipeline.plan_retry(_files(), _previous(**{P7: "stale"}))
    assert P7 in plan["needed"]
    assert P9 in plan["invalidated"]


def test_plan_does_not_duplicate_nodes():
    plan = pipeline.plan_retry(_files(), _previous(**{P1: "failed", P9: "failed"}))
    assert len(plan["run"]) == len(set(plan["run"]))


# --- start_retry integration ------------------------------------------------


@pytest.fixture()
def retry_env(tmp_path, monkeypatch):
    from web import settings as web_settings

    round_dir = tmp_path / "output" / pipeline.PIPELINE_DIR / "2026-09-24"
    round_dir.mkdir(parents=True)
    for _key, filename, _label in pipeline.STAGES:
        (round_dir / filename).write_text("x", encoding="utf-8")
    # P1 "failed" in the previous attempt, everything else succeeded
    os.remove(round_dir / pipeline._STAGE_FILE[P1])
    (round_dir / pipeline._STAGE_FILE[P1]).write_text("x", encoding="utf-8")

    # Previous attempt: everything succeeded except P1, which failed.
    previous_rounds = {
        "2026-09-24": {
            "date": "2026-09-24",
            "status": "partial",
            "trigger": "test",
            "started_at": "2026-09-24T06:00:00",
            "finished_at": "2026-09-24T06:30:00",
            "cancel_requested": False,
            "stages": {
                key: {
                    "key": key,
                    "label": pipeline._STAGE_LABEL[key],
                    "file": pipeline._STAGE_FILE[key],
                    "group": next(g for g, keys in pipeline.GROUPS if key in keys),
                    "status": "failed" if key == P1 else "success",
                }
                for key, _f, _l in pipeline.STAGES
            },
        }
    }
    monkeypatch.setattr(pipeline, "_rounds", previous_rounds)
    monkeypatch.setattr(pipeline, "_persist", lambda state: None)

    captured: dict = {}

    def fake_run_round(state, config_dir, jobs_dir, concurrency, stage_filter=None):
        captured["filter"] = set(stage_filter or set())
        # Snapshot before the stub "runs" the stages: this is what the UI shows
        # at the moment the retry starts.
        captured["status_at_start"] = {
            key: stage["status"] for key, stage in state["stages"].items()
        }
        for key in captured["filter"]:
            pipeline._set_stage(state, key, "success")
        with pipeline._lock:
            state["status"] = "success"

    # The retry thread itself runs (against the stub) so the test can observe
    # which stages were scheduled; no real job is executed.
    monkeypatch.setattr(pipeline, "_run_round", fake_run_round)
    return {"tmp": tmp_path, "captured": captured, "date": "2026-09-24"}


def _wait_for_capture(captured: dict, timeout: float = 5.0) -> set:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if "filter" in captured:
            return captured["filter"]
        time.sleep(0.05)
    raise AssertionError("retry thread never ran")


def test_start_retry_runs_downstream_stages(retry_env):
    tmp = retry_env["tmp"]
    result = pipeline.start_retry(
        config_dir=str(tmp / "config"),
        jobs_dir=str(tmp / "jobs"),
        output_dir=str(tmp / "output"),
        date=retry_env["date"],
    )
    ran = _wait_for_capture(retry_env["captured"])
    assert P1 in ran, "the failed stage must run"
    assert {P7, P8, P9} <= ran, "consumers of the re-run stage must be invalidated"
    assert P0 not in ran
    assert result["invalidated_stages"] == [P7, P8, P9]
    assert result["rerun_stages"] == [P1]


def test_start_retry_marks_invalidated_stages_stale(retry_env):
    tmp = retry_env["tmp"]
    pipeline.start_retry(
        config_dir=str(tmp / "config"),
        jobs_dir=str(tmp / "jobs"),
        output_dir=str(tmp / "output"),
        date=retry_env["date"],
    )
    _wait_for_capture(retry_env["captured"])
    at_start = retry_env["captured"]["status_at_start"]
    assert at_start[P7] == "stale", "consumers must start out marked stale"
    assert at_start[P1] == "pending", "the failed stage itself is pending"
    assert at_start[P2] == "skipped", "untouched stages stay skipped"

    with pipeline._lock:
        stages = pipeline._rounds[retry_env["date"]]["stages"]
    assert stages[P7]["invalidated"] is True
    assert stages[P2].get("invalidated") is False


def test_start_retry_can_skip_invalidation(retry_env):
    tmp = retry_env["tmp"]
    result = pipeline.start_retry(
        config_dir=str(tmp / "config"),
        jobs_dir=str(tmp / "jobs"),
        output_dir=str(tmp / "output"),
        date=retry_env["date"],
        invalidate_downstream=False,
    )
    assert _wait_for_capture(retry_env["captured"]) == {P1}
    assert result["invalidated_stages"] == []


def test_start_retry_rejects_completed_round(retry_env, tmp_path):
    with pipeline._lock:
        for stage in pipeline._rounds[retry_env["date"]]["stages"].values():
            stage["status"] = "success"
    with pytest.raises(RuntimeError, match="无需补跑"):
        pipeline.start_retry(
            config_dir=str(tmp_path / "config"),
            jobs_dir=str(tmp_path / "jobs"),
            output_dir=str(tmp_path / "output"),
            date=retry_env["date"],
        )
