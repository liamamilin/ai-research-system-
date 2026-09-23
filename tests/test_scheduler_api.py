"""Tests for the schedulable-jobs endpoint."""

from __future__ import annotations

import pytest


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _write_jobs(web_env):
    tmp_path, _ = web_env
    jobs_dir = tmp_path / "jobs"
    (jobs_dir / "research").mkdir(parents=True, exist_ok=True)
    (jobs_dir / "research" / "radar.yaml").write_text(
        'name: "Daily Radar"\ndescription: "track model releases"\n'
        'enabled: true\nschedule:\n  type: "daily"\nprompt: "x"\n',
        encoding="utf-8",
    )
    (jobs_dir / "old.yaml").write_text(
        'name: "Old"\ndescription: "legacy"\nenabled: false\nprompt: "x"\n',
        encoding="utf-8",
    )
    return tmp_path


def test_requires_admin(client, web_env):
    assert client.get("/api/scheduler/jobs").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/scheduler/jobs").status_code == 403


def test_lists_jobs_with_command(client, web_env):
    _write_jobs(web_env)
    _login(client)

    r = client.get("/api/scheduler/jobs")
    assert r.status_code == 200
    body = r.json()
    by_name = {job["name"]: job for job in body["jobs"]}

    assert set(by_name) == {"research/radar", "old"}
    radar = by_name["research/radar"]
    assert radar["label"] == "Daily Radar"
    assert radar["description"] == "track model releases"
    assert radar["enabled"] is True
    assert radar["schedule_type"] == "daily"
    assert "run.py research/radar" in radar["command"]
    assert "cron_research_radar.log" in radar["command"]
    assert by_name["old"]["enabled"] is False
    assert body["project_dir"]


def test_empty_jobs_dir_returns_empty_list(client, web_env):
    _login(client)
    r = client.get("/api/scheduler/jobs")
    assert r.status_code == 200
    assert r.json()["jobs"] == []
