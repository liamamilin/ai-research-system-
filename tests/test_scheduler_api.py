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


@pytest.fixture
def isolated_cron(monkeypatch):
    from web.services import scheduler
    store = {"lines": ["# unrelated", "MAILTO=somebody", "# cron_id: report", "# 0 8 * * * echo old"]}
    monkeypatch.setattr(scheduler, "_get_crontab", lambda: list(store["lines"]))
    monkeypatch.setattr(scheduler, "_set_crontab", lambda lines: store.update(lines=list(lines)))
    return store


def test_preview_is_read_only_and_validates(client, isolated_cron):
    _login(client)
    before = list(isolated_cron["lines"])
    r = client.get("/api/scheduler/preview", params={"schedule": "0 8 * * 1-5"})
    assert r.status_code == 200
    assert len(r.json()["next_runs"]) == 3
    assert r.json()["timezone"]
    assert client.get("/api/scheduler/preview", params={"schedule": "0 25 * * *"}).status_code == 422
    assert isolated_cron["lines"] == before


def test_edit_pause_and_conflict_contract(client, isolated_cron):
    _login(client)
    r = client.put("/api/scheduler/report", headers=_csrf(client), json={"schedule": "0 9 * * 1-5", "command": "echo new"})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert isolated_cron["lines"][:2] == ["# unrelated", "MAILTO=somebody"]
    assert isolated_cron["lines"][-1] == "# 0 9 * * 1-5 echo new"
    assert client.put("/api/scheduler/report/toggle", headers=_csrf(client), json={"enabled": False}).status_code == 200
    assert client.put("/api/scheduler/report/toggle", headers=_csrf(client), json={"enabled": "false"}).status_code == 422
    assert client.post("/api/scheduler", headers=_csrf(client), json={"id": "report", "schedule": "0 9 * * *", "command": "echo x"}).status_code == 422
    assert client.post("/api/scheduler", headers=_csrf(client), json={"id": "other", "schedule": "0 28 * * *", "command": "echo x"}).status_code == 422


def test_launchd_is_visible_but_not_sent_to_cron_mutations(client, isolated_cron, monkeypatch):
    from web.services import scheduler
    monkeypatch.setattr(scheduler, "list_launchd_agents", lambda: [{
        "id": "com.arec.pipeline.daily", "backend": "launchd", "hour": "6", "minute": "0",
        "command": "ensure_round.py", "enabled": True, "loaded": True,
    }])
    _login(client)
    r = client.get("/api/scheduler")
    assert r.status_code == 200
    agent = next(j for j in r.json()["jobs"] if j["backend"] == "launchd")
    assert agent["editable"] is False and agent["next_runs"]
    assert client.put("/api/scheduler/com.arec.pipeline.daily/toggle", headers=_csrf(client), json={"enabled": False}).status_code == 409
