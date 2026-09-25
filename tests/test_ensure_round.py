"""The catch-up runner that makes the daily round independent of any trigger."""

from __future__ import annotations

import json
import time

import pytest

from scripts import ensure_round


@pytest.fixture()
def state(tmp_path, monkeypatch):
    """Point the runner at a temporary state directory."""
    directory = tmp_path / "state"
    directory.mkdir()
    monkeypatch.setattr(ensure_round, "STATE_DIR", str(directory))
    monkeypatch.setattr(ensure_round, "HEARTBEAT", str(directory / "scheduler_heartbeat.json"))
    monkeypatch.setattr(ensure_round, "ROUNDS", str(directory / "pipeline_rounds.json"))
    return directory


def _rounds(path, date, status, trigger="launchd"):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({date: {"date": date, "status": status, "trigger": trigger}}, fh)


def test_missing_round_is_started(state):
    should_run, reason = ensure_round.decide(state={"exists": False})
    assert should_run is True
    assert "无轮次记录" in reason


def test_finished_round_is_left_alone(state):
    should_run, reason = ensure_round.decide(
        state={"exists": True, "status": "success", "finished": True})
    assert should_run is False
    assert "已完成" in reason


def test_running_round_is_not_started_twice(state):
    should_run, reason = ensure_round.decide(
        state={"exists": True, "status": "running", "running": True})
    assert should_run is False
    assert "正在运行" in reason


def test_failed_round_is_retried(state):
    should_run, _ = ensure_round.decide(
        state={"exists": True, "status": "failed", "finished": False})
    assert should_run is True


def test_exhausted_budget_blocks_the_run(state):
    should_run, reason = ensure_round.decide(
        state={"exists": False}, budget_allowed=False, budget_note="$5 / $5")
    assert should_run is False
    assert "预算" in reason


def test_heartbeat_records_every_check(state):
    ensure_round.write_heartbeat("skipped", "今日轮次已完成（success）")
    beat = json.loads((state / "scheduler_heartbeat.json").read_text())
    assert beat["status"] == "skipped"
    assert beat["detail"] == "今日轮次已完成（success）"
    age = ensure_round.heartbeat_age()
    assert age is not None and age < 5


def test_heartbeat_keeps_the_last_run_separate(state):
    ensure_round.write_heartbeat("ok", "轮次完成", ran=True)
    first = json.loads((state / "scheduler_heartbeat.json").read_text())
    time.sleep(0.01)
    ensure_round.write_heartbeat("skipped", "今日轮次已完成（success）")
    second = json.loads((state / "scheduler_heartbeat.json").read_text())
    assert second["last_run"] == first["last_run"]
    assert second["last_run_iso"] == first["last_run_iso"]
    assert second["checked_at"] >= first["checked_at"]


def test_round_state_reads_the_registry(state):
    _rounds(ensure_round.ROUNDS, "2026-09-25", "success")
    info = ensure_round.round_state("2026-09-25")
    assert info["exists"] and info["finished"]
    assert ensure_round.round_state("2026-09-26")["exists"] is False


def test_heartbeat_age_is_none_without_a_heartbeat(state):
    assert ensure_round.heartbeat_age() is None


def test_a_stale_running_round_is_retried(state):
    """A killed process must not wedge every future catch-up.

    Reinstalling the launchd agent runs `launchctl bootout`, which takes the
    running pipeline with it and leaves status="running" in the registry
    forever.
    """
    with open(ensure_round.ROUNDS, "w", encoding="utf-8") as fh:
        json.dump({"2026-09-25": {
            "date": "2026-09-25", "status": "running", "trigger": "launchd",
            "started_at": "2026-09-25T00:30:00+0800",
        }}, fh)
    info = ensure_round.round_state("2026-09-25")
    assert info["stale"] is True
    assert info["running"] is False
    should_run, reason = ensure_round.decide(state=info)
    assert should_run is True
    assert "超时" in reason


def test_a_fresh_running_round_is_not_retried(state):
    with open(ensure_round.ROUNDS, "w", encoding="utf-8") as fh:
        json.dump({"2026-09-25": {
            "date": "2026-09-25", "status": "running", "trigger": "launchd",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }}, fh)
    info = ensure_round.round_state("2026-09-25")
    assert info["stale"] is False
    should_run, reason = ensure_round.decide(state=info)
    assert should_run is False
    assert "正在运行" in reason
