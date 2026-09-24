"""User-activity heartbeat, consumed by the AREC launcher's idle watchdog.

The launcher closes itself when nobody has used the console for a while *and*
no job is running. Both facts have to come from somewhere the applet can read
without a JSON parser, which is why this module also writes a key=value file.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from core import activity


@pytest.fixture()
def state_dir(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(activity, "_state_dir", str(state))
    monkeypatch.setattr(activity, "_last_write", 0.0)
    monkeypatch.setattr(activity, "_dirty", False)
    monkeypatch.setattr(activity, "_last_activity", "")
    monkeypatch.setattr(activity, "_running", 0)
    yield str(state)
    activity.use_state_dir("state")


def test_marking_activity_writes_both_files(state_dir):
    activity.mark_activity(force=True)

    data = json.loads(open(os.path.join(state_dir, "app_activity.json"),
                           encoding="utf-8").read())
    assert data["last_activity"]
    assert data["running"] == 0

    env = open(os.path.join(state_dir, "app_activity.env"), encoding="utf-8").read()
    assert "last_activity=" in env
    assert "running=0" in env


def test_env_timestamp_uses_the_shape_apple_script_parses(state_dir):
    """AppleScript's date parser only accepts "YYYY-MM-DD HH:MM:SS" in any locale."""
    activity.mark_activity(force=True)
    env = open(os.path.join(state_dir, "app_activity.env"), encoding="utf-8").read()
    stamp = [line for line in env.splitlines() if line.startswith("last_activity=")][0]
    value = stamp.split("=", 1)[1]
    parsed = time.strptime(value, "%Y-%m-%d %H:%M:%S")
    assert parsed.tm_year >= 2024


def test_writes_are_throttled_but_forced_writes_always_land(state_dir):
    activity.mark_activity(force=True)
    first = open(os.path.join(state_dir, "app_activity.env"), encoding="utf-8").read()

    # A burst of page-load requests must not rewrite the file every time.
    for _ in range(5):
        activity.mark_activity()
    assert open(os.path.join(state_dir, "app_activity.env"),
                encoding="utf-8").read() == first

    # A run starting must be visible immediately, not after the throttle window.
    activity.set_running(2)
    assert "running=2" in open(os.path.join(state_dir, "app_activity.env"),
                               encoding="utf-8").read()


def test_running_count_is_published(state_dir):
    activity.set_running(3)
    assert activity.read().get("running") == 3
    assert "running=3" in open(os.path.join(state_dir, "app_activity.env"),
                               encoding="utf-8").read()


def test_idle_seconds_reflects_the_recorded_activity(state_dir, monkeypatch):
    activity.mark_activity(force=True)
    now = time.time()
    assert activity.idle_seconds(now=now) < 5

    past = now - 45 * 60
    with open(os.path.join(state_dir, "app_activity.env"), "w", encoding="utf-8") as fh:
        fh.write("last_activity=%s\nrunning=0\n"
                 % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(past)))
    assert 2600 < activity.idle_seconds(now=now) < 2800


def test_idle_seconds_is_none_when_nothing_was_ever_recorded(state_dir):
    assert activity.idle_seconds() is None
    assert activity.read() == {}


def test_use_state_dir_moves_both_files(tmp_path, monkeypatch):
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()

    activity.use_state_dir(str(first))
    activity.mark_activity(force=True)
    assert os.path.isfile(os.path.join(str(first), "app_activity.env"))

    activity.use_state_dir(str(second))
    activity.set_running(1)
    second_file = os.path.join(str(second), "app_activity.env")
    assert os.path.isfile(second_file)
    assert "running=1" in open(second_file, encoding="utf-8").read()
    # The old location keeps its last state; switching directories redirects
    # writes, it does not clean up the previous one.
    assert os.path.isfile(os.path.join(str(first), "app_activity.env"))
