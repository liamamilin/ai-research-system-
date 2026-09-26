"""Editing, pausing and deleting the two launchd matrix agents.

Every test drives a throwaway agent directory and a fake launchctl: the real
``~/Library/LaunchAgents`` and the machine's own schedule must never be touched
by the suite (same rule as the rest of the scheduler tests).
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from web.services import launchd_agents as la

DAILY = "com.arec.pipeline.daily"
CATCHUP = "com.arec.pipeline.catchup"


def _config(label: str) -> dict:
    """The plist shape scripts/launchd/*.plist installs."""
    base = {
        "Label": label,
        "ProgramArguments": ["/usr/bin/python3", "/repo/scripts/ensure_round.py"],
        "RunAtLoad": True,
        "StandardOutPath": "/repo/logs/launchd.log",
    }
    if la.KINDS[label] == "daily":
        base["StartCalendarInterval"] = {"Hour": 6, "Minute": 0}
    else:
        base["StartInterval"] = 1800
    return base


@pytest.fixture()
def agents(tmp_path, monkeypatch):
    """A temp LaunchAgents dir plus a launchctl that records what it was told."""
    directory = tmp_path / "LaunchAgents"
    directory.mkdir()
    for label in la.LABELS:
        (directory / f"{label}.plist").write_bytes(plistlib.dumps(_config(label)))

    calls: list[tuple[str, ...]] = []
    loaded: set[str] = set(la.LABELS)

    def fake_run(cmd, **kwargs):
        # /bin/launchctl print gui/<uid>/<label> | bootstrap | bootout
        args = list(cmd[1:]) if cmd and cmd[0].endswith("launchctl") else list(cmd)
        if args and args[0] == "print":
            label = args[-1].split("/")[-1]
            return _Result(0 if label in loaded else 113, "" if label in loaded else "not found")
        calls.append(tuple(args))
        if args and args[0] == "bootstrap":
            loaded.add(Path(args[-1]).stem)
            return _Result(0, "")
        if args and args[0] == "bootout":
            loaded.discard(args[-1].split("/")[-1])
            return _Result(0, "")
        return _Result(0, "")

    class _Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    monkeypatch.setattr(la.subprocess, "run", fake_run)
    monkeypatch.setattr(la.sys, "platform", "darwin")
    monkeypatch.setattr(la, "agent_dir", lambda: directory)

    class Env:
        path = directory
        # A class body is not a closure scope, so an attribute may not reuse the
        # fixture's local name (`loaded = loaded` raises NameError here).
        loaded_labels = loaded
        launchctl_calls = calls

        @staticmethod
        def config(label):
            return la.read_config(label, directory)

        @staticmethod
        def cron(label):
            return la.to_cron(label, la.read_config(label, directory))

    Env._Result = _Result
    return Env


# --- cron <-> plist projection -------------------------------------------------

def test_daily_projects_to_a_time_of_day(agents):
    assert agents.cron(DAILY) == "0 6 * * *"


def test_catchup_projects_to_an_interval(agents):
    assert agents.cron(CATCHUP) == "*/30 * * * *"


def test_missing_plist_has_no_schedule(agents):
    (agents.path / f"{DAILY}.plist").unlink()
    assert la.read_config(DAILY, agents.path) == {}
    assert la.to_cron(DAILY, {}) == ""


def test_unreadable_plist_is_not_fatal(agents):
    (agents.path / f"{DAILY}.plist").write_bytes(b"not a plist at all")
    assert la.read_config(DAILY, agents.path) == {}


# --- validation: nothing is written when the expression is unusable ------------

@pytest.mark.parametrize("expression", ["", "   ", "0 6 * *", "0 6 * * 1", "abc def * * *",
                                         "0 99 * * *", "60 6 * * *", "*/15 0 6 * *"])
def test_bad_daily_expression_is_rejected_without_touching_the_plist(agents, expression):
    with pytest.raises(ValueError):
        la.apply_cron(DAILY, expression, agents.path)
    assert agents.config(DAILY)["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert agents.launchctl_calls == []


@pytest.mark.parametrize("expression", ["0 6 * * *", "30", "*/0 * * * *", "*/361 * * * *",
                                         "*/5 * * * 1", "*/ * * * *"])
def test_bad_catchup_expression_is_rejected_without_touching_the_plist(agents, expression):
    with pytest.raises(ValueError):
        la.apply_cron(CATCHUP, expression, agents.path)
    assert agents.config(CATCHUP)["StartInterval"] == 1800
    assert agents.launchctl_calls == []


def test_unknown_label_is_refused(agents):
    with pytest.raises(la.LaunchdError):
        la.apply_cron("com.arec.something.else", "0 6 * * *", agents.path)


# --- editing ------------------------------------------------------------------

def test_editing_the_daily_time_rewrites_the_calendar_interval(agents):
    la.apply_cron(DAILY, "30 7 * * *", agents.path)
    assert agents.config(DAILY)["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert agents.cron(DAILY) == "30 7 * * *"


def test_editing_the_interval_rewrites_start_interval(agents):
    la.apply_cron(CATCHUP, "*/10 * * * *", agents.path)
    assert agents.config(CATCHUP)["StartInterval"] == 600


def test_editing_keeps_every_other_key(agents):
    la.apply_cron(DAILY, "0 9 * * *", agents.path)
    config = agents.config(DAILY)
    assert config["ProgramArguments"] == ["/usr/bin/python3", "/repo/scripts/ensure_round.py"]
    assert config["RunAtLoad"] is True
    assert config["StandardOutPath"] == "/repo/logs/launchd.log"


def test_switching_a_daily_agent_away_from_a_time_clears_the_stale_trigger(agents):
    # An agent carrying both triggers fires on whichever comes first, which is
    # not what the operator just asked for.
    config = agents.config(DAILY)
    config["StartInterval"] = 600
    (agents.path / f"{DAILY}.plist").write_bytes(plistlib.dumps(config))
    la.apply_cron(DAILY, "15 5 * * *", agents.path)
    assert "StartInterval" not in agents.config(DAILY)
    assert agents.config(DAILY)["StartCalendarInterval"] == {"Hour": 5, "Minute": 15}


def test_edit_reloads_the_agent_so_the_new_time_takes_effect(agents):
    la.apply_cron(DAILY, "0 8 * * *", agents.path)
    verbs = [call[0] for call in agents.launchctl_calls]
    assert "bootout" in verbs and "bootstrap" in verbs


def test_edit_rolls_the_plist_back_when_the_reload_fails(agents, monkeypatch):
    original = (agents.path / f"{DAILY}.plist").read_bytes()
    real_run = la.subprocess.run

    def fail_bootstrap(cmd, **kwargs):
        if isinstance(cmd, list) and "bootstrap" in cmd:
            return agents._Result(1, "", "Load failed: 5: Input/output error")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(la.subprocess, "run", fail_bootstrap)
    with pytest.raises(la.LaunchdError):
        la.apply_cron(DAILY, "0 8 * * *", agents.path)
    # The round must not be left with no trigger and a changed schedule.
    assert (agents.path / f"{DAILY}.plist").read_bytes() == original
    assert agents.cron(DAILY) == "0 6 * * *"


def test_edit_of_an_uninstalled_agent_explains_how_to_install_it(agents):
    (agents.path / f"{DAILY}.plist").unlink()
    with pytest.raises(la.LaunchdError) as excinfo:
        la.apply_cron(DAILY, "0 8 * * *", agents.path)
    assert "install_launchd.sh" in str(excinfo.value)


# --- pause / resume -----------------------------------------------------------

def test_pause_unloads_but_keeps_the_plist(agents):
    la.set_enabled(DAILY, False, agents.path)
    assert DAILY not in agents.loaded_labels
    assert (agents.path / f"{DAILY}.plist").exists()
    assert agents.cron(DAILY) == "0 6 * * *"


def test_resume_loads_the_agent_again(agents):
    la.set_enabled(DAILY, False, agents.path)
    la.set_enabled(DAILY, True, agents.path)
    assert DAILY in agents.loaded_labels


def test_resume_is_idempotent(agents):
    la.set_enabled(DAILY, True, agents.path)
    assert not [c for c in agents.launchctl_calls if c[0] == "bootstrap"]


# --- delete / restore ---------------------------------------------------------

def test_delete_unloads_and_moves_the_plist_into_a_backup(agents, tmp_path):
    state = tmp_path / "state"
    result = la.delete(DAILY, state, agents.path)
    assert not (agents.path / f"{DAILY}.plist").exists()
    assert DAILY not in agents.loaded_labels
    backup = Path(result["backup"])
    assert (backup / f"{DAILY}.plist").is_file()
    assert result["schedule"] == "0 6 * * *"


def test_deleted_agents_are_listed_for_restore(agents, tmp_path):
    state = tmp_path / "state"
    la.delete(CATCHUP, state, agents.path)
    listed = la.list_deleted(state)
    assert [entry["label"] for entry in listed] == [CATCHUP]
    assert listed[0]["schedule"] == "*/30 * * * *"
    assert listed[0]["installed"] is False


def test_restore_puts_the_agent_back_and_loads_it(agents, tmp_path):
    state = tmp_path / "state"
    la.delete(DAILY, state, agents.path)
    la.restore(DAILY, state, agents.path)
    assert (agents.path / f"{DAILY}.plist").is_file()
    assert DAILY in agents.loaded_labels
    assert agents.cron(DAILY) == "0 6 * * *"
    assert la.list_deleted(state) == []


def test_restore_without_a_backup_says_so(agents, tmp_path):
    with pytest.raises(la.LaunchdError) as excinfo:
        la.restore(DAILY, tmp_path / "state", agents.path)
    assert "没有找到" in str(excinfo.value)


def test_restore_refuses_to_overwrite_a_live_agent(agents, tmp_path):
    state = tmp_path / "state"
    original = (agents.path / f"{DAILY}.plist").read_bytes()
    la.delete(DAILY, state, agents.path)
    (agents.path / f"{DAILY}.plist").write_bytes(original)
    with pytest.raises(la.LaunchdError) as excinfo:
        la.restore(DAILY, state, agents.path)
    assert "已经存在" in str(excinfo.value)


def test_listing_deleted_ignores_unrelated_and_broken_entries(agents, tmp_path):
    state = tmp_path / "state"
    la.delete(DAILY, state, agents.path)
    root = state / "scheduler_backup" / "20200101_000000"
    root.mkdir(parents=True)
    (root / "meta.json").write_text('{"label": "com.arec.other"}', encoding="utf-8")
    (state / "scheduler_backup" / "20200102_000000").mkdir(parents=True)
    assert [entry["label"] for entry in la.list_deleted(state)] == [DAILY]


def test_no_backups_reports_nothing_rather_than_failing(agents, tmp_path):
    assert la.list_deleted(tmp_path / "empty") == []
