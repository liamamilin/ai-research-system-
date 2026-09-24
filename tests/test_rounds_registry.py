"""Round registry shared by the web runner and the CLI/cron path."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from core import rounds


@pytest.fixture()
def state_dir(tmp_path):
    d = tmp_path / "state"
    d.mkdir()
    return str(d)


def _path(state_dir):
    return os.path.join(state_dir, "pipeline_rounds.json")


def test_upsert_creates_entry(state_dir):
    entry = rounds.upsert_round("2026-09-24", status="running", trigger="cron", state_dir=state_dir)
    assert entry["status"] == "running"
    assert entry["trigger"] == "cron"
    assert entry["started_at"]
    assert os.path.isfile(_path(state_dir))


def test_upsert_merges_stages_by_key(state_dir):
    rounds.upsert_round("2026-09-24", status="running", trigger="web", state_dir=state_dir,
                        stages=[{"key": "P0", "status": "success", "output": "a.md"}])
    rounds.upsert_round("2026-09-24", status="partial", trigger="cron", state_dir=state_dir,
                        stages=[{"key": "P1", "status": "failed"}, {"key": "P0", "status": "success"}])

    entry = rounds.list_rounds(state_dir)[0]
    assert entry["status"] == "partial"
    assert entry["trigger"] == "web"  # first writer wins unless overridden
    keys = {s["key"] for s in entry["stages"]}
    assert keys == {"P0", "P1"}
    p0 = next(s for s in entry["stages"] if s["key"] == "P0")
    assert p0["output"] == "a.md"  # earlier detail preserved


def test_upsert_keeps_started_at_and_sets_finished(state_dir):
    rounds.upsert_round("2026-09-24", status="running", state_dir=state_dir)
    started = rounds.list_rounds(state_dir)[0]["started_at"]
    rounds.upsert_round("2026-09-24", status="success", state_dir=state_dir,
                        finished_at="2026-09-24T07:00:00+0800")
    entry = rounds.list_rounds(state_dir)[0]
    assert entry["started_at"] == started
    assert entry["finished_at"] == "2026-09-24T07:00:00+0800"


def test_list_rounds_newest_first_and_capped(state_dir):
    for i in range(rounds.KEEP_ROUNDS + 5):
        rounds.upsert_round(f"2026-01-{i + 1:02d}", status="success", state_dir=state_dir)
    listed = rounds.list_rounds(state_dir)
    assert len(listed) == rounds.KEEP_ROUNDS
    assert listed[0]["date"] > listed[-1]["date"]


def test_latest_round(state_dir):
    assert rounds.latest_round(state_dir) is None
    rounds.upsert_round("2026-09-23", status="success", state_dir=state_dir)
    rounds.upsert_round("2026-09-24", status="failed", state_dir=state_dir)
    assert rounds.latest_round(state_dir)["date"] == "2026-09-24"


def test_mark_interrupted(state_dir):
    rounds.upsert_round("2026-09-24", status="running", state_dir=state_dir,
                        stages=[{"key": "P0", "status": "running"},
                                {"key": "P1", "status": "success"}])
    assert rounds.mark_interrupted(state_dir) == 1
    entry = rounds.list_rounds(state_dir)[0]
    assert entry["status"] == "interrupted"
    assert entry["finished_at"]
    assert next(s for s in entry["stages"] if s["key"] == "P0")["status"] == "interrupted"
    assert rounds.mark_interrupted(state_dir) == 0


def test_corrupt_file_is_treated_as_empty(state_dir):
    with open(_path(state_dir), "w", encoding="utf-8") as f:
        f.write("{broken")
    assert rounds.list_rounds(state_dir) == []
    assert rounds.upsert_round("2026-09-24", status="success", state_dir=state_dir)["date"] == "2026-09-24"


def test_file_format_is_date_keyed(state_dir):
    """The web runner and the CLI must share one on-disk format."""
    rounds.upsert_round("2026-09-24", status="success", trigger="cron", state_dir=state_dir)
    with open(_path(state_dir), encoding="utf-8") as f:
        data = json.load(f)
    assert list(data) == ["2026-09-24"]
    assert data["2026-09-24"]["trigger"] == "cron"


def test_infer_stage_status(tmp_path):
    round_dir = tmp_path / "2026-09-24"
    round_dir.mkdir()
    (round_dir / "00_collection_plan.md").write_text("x", encoding="utf-8")
    (round_dir / "01_model_and_pricing_radar.md").write_text("", encoding="utf-8")
    stages = rounds.infer_stage_status(
        str(round_dir),
        {"00_collection_planner": "00_collection_plan.md",
         "01_model_and_pricing_radar": "01_model_and_pricing_radar.md",
         "02_ai_coding_tools_radar": "02_ai_coding_tools_radar.md"},
    )
    by_key = {s["key"]: s for s in stages}
    assert by_key["00_collection_planner"]["status"] == "success"
    assert by_key["01_model_and_pricing_radar"]["status"] == "missing"
    assert by_key["02_ai_coding_tools_radar"]["status"] == "missing"


def test_round_finish_records_round(tmp_path):
    from core.round_finish import finish_round

    output = tmp_path / "output" / "practical_ai_intelligence" / "2026-09-24"
    output.mkdir(parents=True)
    (output / "09_executive_synthesis_and_actions.md").write_text(
        "## 立即行动\n\n| 行动 | 优先级 |\n| --- | --- |\n| A | P0 |\n", encoding="utf-8")
    (output / "00_collection_plan.md").write_text("plan", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()

    summary = finish_round(
        "2026-09-24",
        output_dir=str(tmp_path / "output"),
        state_dir=str(state),
        config_dir=str(tmp_path / "config"),
        notify=False,
    )
    assert summary["round"]["recorded"] is True
    assert summary["round"]["stages_total"] == 10
    assert summary["round"]["stages_ok"] == 2
    assert summary["round"]["status"] == "partial"

    latest = rounds.latest_round(str(state))
    assert latest["trigger"] == "cron"
    assert latest["status"] == "partial"
    assert latest["finished_at"]


def test_round_start_cli_flag(tmp_path, monkeypatch, capsys):
    import run as run_module
    from core import rounds as rounds_mod

    # Point the registry at a temp file: the CLI resolves "state/..." from the CWD.
    tmp_state = tmp_path / "state"
    tmp_state.mkdir()
    monkeypatch.setattr(rounds_mod, "STATE_FILE",
                        str(tmp_state / "pipeline_rounds.json"))
    monkeypatch.setattr("sys.argv", ["run.py", "--round-start", "2026-09-24"])
    monkeypatch.setattr(run_module, "load_system_config", lambda *a, **k: {})
    with pytest.raises(SystemExit) as exc:
        run_module.main()
    assert exc.value.code in (0, 1)
    assert "2026-09-24" in capsys.readouterr().out
    written = json.loads((tmp_state / "pipeline_rounds.json").read_text(encoding="utf-8"))
    assert written["2026-09-24"]["status"] == "running"


def test_round_start_records_a_real_trigger(tmp_path, monkeypatch):
    """A hand-run must not be recorded as a cron fire.

    The trigger used to be hardcoded to "cron", so a manual run satisfied the
    missed-run detector and a dead schedule could look healthy indefinitely.
    """
    import subprocess
    import sys as _sys

    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["AREC_ROUND_TRIGGER"] = "manual"
    env["AI_RESEARCH_ENV"] = "test"

    def run_cli(*args):
        # cwd=tmp_path so "state/" resolves inside the tmp dir: the registry is
        # relative to the working directory and must not touch the real one.
        return subprocess.run(
            [_sys.executable, str(repo / "run.py"), *args],
            capture_output=True, text=True, env=env, cwd=str(tmp_path))

    result = run_cli("--round-start", "2026-09-24")
    assert result.returncode == 0, result.stderr
    assert "trigger=manual" in result.stdout

    import json
    registry = json.loads((tmp_path / "state" / "pipeline_rounds.json").read_text())
    assert registry["2026-09-24"]["trigger"] == "manual"

    # The explicit flag wins over the environment. A new date, because the
    # registry keeps the first trigger of the day on purpose.
    result = run_cli("--round-start", "2026-09-25", "--round-trigger", "cron")
    assert "trigger=cron" in result.stdout
    registry = json.loads((tmp_path / "state" / "pipeline_rounds.json").read_text())
    assert registry["2026-09-25"]["trigger"] == "cron"
