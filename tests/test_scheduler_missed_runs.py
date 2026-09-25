"""Missed-run detection for cron-managed jobs."""

from datetime import datetime, timedelta

import pytest

from web.services import scheduler


@pytest.fixture(autouse=True)
def isolate_launchd(monkeypatch):
    monkeypatch.setattr(scheduler, "list_launchd_agents", lambda: [])

DAILY_6AM = {
    "id": "practical_ai_intelligence",
    "minute": "0",
    "hour": "6",
    "day_of_month": "*",
    "month": "*",
    "day_of_week": "*",
    "command": "bash scripts/run_practical_intelligence.sh >> logs/cron_pipeline.log 2>&1",
    "enabled": True,
}


def test_field_matching_handles_steps_ranges_lists_and_names():
    assert scheduler._field_matches("0", 0, 0, 59) is True
    assert scheduler._field_matches("0", 1, 0, 59) is False
    assert scheduler._field_matches("*/15", 30, 0, 59) is True
    assert scheduler._field_matches("*/15", 31, 0, 59) is False
    assert scheduler._field_matches("9-17", 12, 0, 23) is True
    assert scheduler._field_matches("9-17", 20, 0, 23) is False
    assert scheduler._field_matches("0,30", 30, 0, 59) is True
    assert scheduler._field_matches("mon-fri", 2, 0, 7, scheduler._DOW_NAMES) is True
    assert scheduler._field_matches("mon-fri", 6, 0, 7, scheduler._DOW_NAMES) is False
    assert scheduler._field_matches("bogus", 1, 0, 59) is False


def test_last_fire_before_finds_the_previous_daily_slot():
    now = datetime(2026, 9, 24, 13, 31)
    assert scheduler.last_fire_before(DAILY_6AM, now) == datetime(2026, 9, 24, 6, 0)

    early = datetime(2026, 9, 24, 5, 0)
    assert scheduler.last_fire_before(DAILY_6AM, early) == datetime(2026, 9, 23, 6, 0)


def test_last_fire_before_respects_the_weekly_day():
    weekly = dict(DAILY_6AM, day_of_week="1")  # Mondays
    # 2026-09-24 is a Thursday; previous Monday is 2026-09-21
    assert scheduler.last_fire_before(weekly, datetime(2026, 9, 24, 13, 0)) == \
        datetime(2026, 9, 21, 6, 0)


def _round(state_dir, finished_at):
    """Write a round registry entry the detector can use as evidence."""
    import json
    import os

    path = os.path.join(state_dir, "pipeline_rounds.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"2026-09-24": {
            "round_date": "2026-09-24", "status": "success", "trigger": "cron",
            "started_at": finished_at, "finished_at": finished_at,
            "stages": [],
        }}, fh)


def test_active_job_with_evidence_is_ok(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _round(str(state), "2026-09-24T06:12:00")
    now = datetime(2026, 9, 24, 13, 0)

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(state),
                                     repo_dir=str(tmp_path), now=now)[0]

    assert result["status"] == "ok"
    assert result["last_ran_at"] == "2026-09-24T06:12:00"


def test_active_job_that_did_not_run_is_overdue(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    # Last run was three days ago; the 06:00 slot today passed unclaimed.
    _round(str(state), "2026-09-21T06:10:00")
    now = datetime(2026, 9, 24, 13, 0)

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(state),
                                     repo_dir=str(tmp_path), now=now)[0]

    assert result["status"] == "overdue"
    assert result["missed_hours"] > 48
    assert "没有运行证据" not in result["detail"]
    assert result["last_expected_at"].startswith("2026-09-24T06:00")


def test_grace_period_absorbs_a_slow_run(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    # Started 100 minutes after the slot: still inside the grace window.
    _round(str(state), "2026-09-24T07:40:00")

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(state),
                                     repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 9, 0))[0]
    assert result["status"] == "ok"


def test_paused_job_is_reported_as_paused_not_broken(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _round(str(state), "2026-09-24T06:12:00")

    result = scheduler.classify_jobs([dict(DAILY_6AM, enabled=False)],
                                     state_dir=str(state), repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 13, 0))[0]

    assert result["status"] == "paused"
    # a paused job is not broken: it is not supposed to run
    assert "last_ran_at" not in result


def test_unparsable_schedule_is_not_guessed(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    # A schedule that never fires within the lookback window.
    job = dict(DAILY_6AM, hour="4", day_of_month="31", month="2")

    result = scheduler.classify_jobs([job], state_dir=str(state), repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 13, 0))[0]
    assert result["status"] == "no_schedule"
    assert "无法解析" in result["detail"]


def test_job_without_evidence_source_is_unverified(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    job = dict(DAILY_6AM, command="cd /tmp && /usr/bin/true")

    result = scheduler.classify_jobs([job], state_dir=str(state), repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 13, 0))[0]
    assert result["status"] == "unverified"


def test_log_file_is_evidence_when_no_round_is_registered(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    log = logs / "cron_pipeline.log"
    log.write_text("done\n", encoding="utf-8")
    stamp = datetime(2026, 9, 24, 6, 5).timestamp()
    import os
    os.utime(log, (stamp, stamp))

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(tmp_path / "state"),
                                     repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 13, 0))[0]

    assert result["status"] == "ok"
    assert result["evidence"]["source"] == "logs/cron_pipeline.log"


def test_schedule_health_reports_error_for_overdue(monkeypatch, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _round(str(state), "2026-09-01T06:10:00")
    monkeypatch.setattr(scheduler, "list_jobs", lambda: [DAILY_6AM])

    report = scheduler.schedule_health(state_dir=str(state), repo_dir=str(tmp_path),
                                       now=datetime(2026, 9, 24, 13, 0))

    assert report["status"] == "error"
    assert report["overdue"] == 1
    assert "practical_ai_intelligence" in report["detail"]


def test_schedule_health_reports_warn_for_paused(monkeypatch, tmp_path):
    monkeypatch.setattr(scheduler, "list_jobs", lambda: [dict(DAILY_6AM, enabled=False)])

    report = scheduler.schedule_health(state_dir=str(tmp_path), repo_dir=str(tmp_path),
                                       now=datetime(2026, 9, 24, 13, 0))

    assert report["status"] == "warn"
    assert report["paused"] == 1


def test_schedule_health_survives_an_unreadable_crontab(monkeypatch, tmp_path):
    def boom():
        raise OSError("crontab not available")

    monkeypatch.setattr(scheduler, "list_jobs", boom)
    report = scheduler.schedule_health(state_dir=str(tmp_path))
    assert report["status"] == "unknown"
    assert report["jobs"] == []


def test_pipeline_evidence_is_json_serializable(tmp_path):
    """Regression: the evidence dict used to contain itself.

    ``newest["all_signals"] = signals`` assigned the same object that was
    already a member of ``signals``, so FastAPI's encoder recursed until the
    stack blew and /api/health/detailed returned 500.
    """
    import json

    state = tmp_path / "state"
    state.mkdir()
    _round(str(state), "2026-09-24T18:24:16+0800")

    evidence = scheduler._pipeline_evidence(
        "bash scripts/run_practical_intelligence.sh", str(state), str(tmp_path))

    assert evidence is not None
    encoded = json.dumps(evidence)  # must not recurse
    assert "pipeline_rounds" in encoded
    # And the nested list must not point back at its own member
    assert all(item is not evidence for item in evidence["all_signals"])


def test_latest_round_wins_regardless_of_registry_order(tmp_path):
    """The newest run decides, not the first entry the registry returns."""
    import json
    import os

    state = tmp_path / "state"
    state.mkdir()
    # An older round keyed by a *later* date: ordering by key would pick it.
    with open(os.path.join(state, "pipeline_rounds.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "2026-09-24": {"date": "2026-09-24", "finished_at": "2026-09-24T18:24:16+0800"},
            "2026-09-19": {"date": "2026-09-19", "finished_at": "2026-09-20T12:43:04"},
        }, fh)

    evidence = scheduler._pipeline_evidence(
        "bash scripts/run_practical_intelligence.sh", str(state), str(tmp_path))
    assert evidence["at"].startswith("2026-09-24")


def test_timestamps_with_and_without_offsets_compare(tmp_path):
    """A log mtime and a round timestamp must be comparable."""
    state = tmp_path / "state"
    state.mkdir()
    _round(str(state), "2026-09-24T18:24:16+0800")

    result = scheduler.classify_jobs(
        [DAILY_6AM], state_dir=str(state), repo_dir=str(tmp_path),
        now=datetime(2026, 9, 24, 20, 0))[0]

    assert result["status"] == "ok"
    assert result["last_ran_at"].startswith("2026-09-24")


def test_a_manual_round_does_not_satisfy_the_cron_watchdog(tmp_path):
    """Running the pipeline by hand says nothing about cron.

    The registry used to hardcode trigger="cron", so a daily manual run kept the
    missed-run detector reporting "no missed runs" while the schedule itself
    could be dead for weeks.
    """
    import json
    import os

    state = tmp_path / "state"
    state.mkdir()
    with open(os.path.join(state, "pipeline_rounds.json"), "w", encoding="utf-8") as fh:
        json.dump({"2026-09-24": {
            "date": "2026-09-24", "status": "success", "trigger": "manual",
            "finished_at": "2026-09-24T18:24:16+0800",
        }}, fh)

    evidence = scheduler._pipeline_evidence(
        "bash scripts/run_practical_intelligence.sh", str(state), str(tmp_path))
    assert evidence["cron_evidence"] is False

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(state),
                                     repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 20, 0))[0]
    assert result["status"] == "unverified"
    assert "手动运行" in result["detail"]
    assert "不能证明" in result["detail"]


def test_a_cron_round_does_satisfy_the_watchdog(tmp_path, monkeypatch):
    import json
    import os

    # The watchdog asks the real crontab and the real launchd whether the
    # command is installed; both are this machine's business, not the test's.
    monkeypatch.setattr(scheduler, "_get_crontab", lambda: [
        "# cron_id: practical_ai_intelligence",
        "0 6 * * * bash scripts/run_practical_intelligence.sh",
    ])
    monkeypatch.setattr(scheduler, "list_launchd_agents", lambda: [])

    state = tmp_path / "state"
    state.mkdir()
    with open(os.path.join(state, "pipeline_rounds.json"), "w", encoding="utf-8") as fh:
        json.dump({"2026-09-24": {
            "date": "2026-09-24", "status": "success", "trigger": "cron",
            "finished_at": "2026-09-24T06:24:16+0800",
        }}, fh)

    result = scheduler.classify_jobs([DAILY_6AM], state_dir=str(state),
                                     repo_dir=str(tmp_path),
                                     now=datetime(2026, 9, 24, 20, 0))[0]
    assert result["status"] == "ok"


def test_a_header_without_a_schedule_line_is_reported(tmp_path, monkeypatch):
    """A rewritten crontab can keep the comment and lose the schedule."""
    monkeypatch.setattr(scheduler, "_get_crontab", lambda: [
        "# Practical AI Intelligence - daily 06:00",
        "# cron_id: practical_ai_intelligence",
        "",
    ])
    # On a machine that runs launchd, the real agents would be discovered here
    # and mask the truncated crontab this test is about.
    monkeypatch.setattr(scheduler, "list_launchd_agents", lambda: [])
    assert scheduler.orphan_headers() == ["practical_ai_intelligence"]

    report = scheduler.schedule_health(state_dir=str(tmp_path), repo_dir=str(tmp_path))
    assert report["status"] == "error"
    assert "只剩注释头" in report["detail"]
    assert report["orphan_headers"] == ["practical_ai_intelligence"]


def test_healthy_crontab_has_no_orphan_headers(monkeypatch):
    monkeypatch.setattr(scheduler, "_get_crontab", lambda: [
        "# cron_id: job_a",
        "0 6 * * * bash a.sh",
    ])
    assert scheduler.orphan_headers() == []


def test_individual_job_does_not_borrow_pipeline_evidence(tmp_path):
    import json
    state = tmp_path / "state"
    state.mkdir()
    (state / "pipeline_rounds.json").write_text(json.dumps({"2026-09-24": {
        "date": "2026-09-24", "status": "success", "trigger": "cron",
        "finished_at": "2026-09-24T06:30:00",
    }}))
    job = dict(DAILY_6AM, command="python run.py some_other_job >> logs/cron_other.log 2>&1")
    result = scheduler.classify_jobs([job], state_dir=str(state), repo_dir=str(tmp_path), now=datetime(2026, 9, 24, 12))[0]
    assert result["status"] == "unverified"


def test_frequent_schedule_cannot_hide_forever_in_grace_window(monkeypatch):
    monkeypatch.setattr(scheduler, "_evidence_for", lambda *args: {"at": "2026-09-24T06:00:00", "kind": "log"})
    job = dict(DAILY_6AM, minute="*/15", hour="*")
    result = scheduler.classify_jobs([job], now=datetime(2026, 9, 24, 12))[0]
    assert result["status"] == "overdue"


def test_catchup_heartbeat_does_not_certify_daily_trigger(tmp_path):
    import json
    now = datetime(2026, 9, 25, 12)
    (tmp_path / "scheduler_heartbeat.json").write_text(json.dumps({"checked_at": now.timestamp(), "status": "ok"}))
    jobs = [{"id": f"com.arec.pipeline.{kind}", "backend": "launchd", "enabled": True, "loaded": True, "hour": "6", "minute": "0"} for kind in ("daily", "catchup")]
    states = scheduler._classify_launchd(jobs, str(tmp_path), now)
    assert states[0]["status"] == "unverified"
    assert states[1]["status"] == "ok"
