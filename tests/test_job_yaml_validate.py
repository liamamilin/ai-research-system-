"""Preflight validation for job YAML edits (no save)."""

from __future__ import annotations

import os

import pytest

JOB = "monitoring/daily_ai_agents"

JOB_YAML = """name: "Daily AI Agents"
description: "watch the agents space"
enabled: true
prompt: |
  Track {name}.
output: "output/monitoring/{name}_{date}.md"
"""

VALID = """name: "Test Job"
description: "a job"
enabled: true
keywords: [ai, agents]
language: "zh"
prompt: |
  Research {name} about {keywords}.
output: "output/research/{name}_{date}.md"
"""


@pytest.fixture(autouse=True)
def job_on_disk(web_env):
    jobs_dir = os.path.join(str(web_env[0]), "jobs")
    target = os.path.join(jobs_dir, "monitoring")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "daily_ai_agents.yaml"), "w", encoding="utf-8") as fh:
        fh.write(JOB_YAML)
    yield


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _headers(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _validate(client, yaml_content: str):
    return client.post(f"/api/jobs/{JOB}/validate",
                       json={"yaml_content": yaml_content}, headers=_headers(client))


def test_valid_yaml_passes(client, web_env):
    _login(client)
    body = _validate(client, VALID).json()
    assert body["ok"] is True
    assert body["errors"] == []


def test_broken_yaml_reports_the_parser_error(client, web_env):
    _login(client)
    body = _validate(client, "name: [unclosed\n  bad: :").json()
    assert body["ok"] is False
    assert body["errors"]


def test_output_escape_is_blocked_before_saving(client, web_env):
    _login(client)
    body = _validate(client, VALID.replace(
        'output/research/{name}_{date}.md', "../../../etc/passwd")).json()
    assert body["ok"] is False
    assert any("output" in e.lower() or "路径" in e for e in body["errors"])


def test_empty_prompt_is_an_error(client, web_env):
    _login(client)
    body = _validate(client,
                     'name: "x"\noutput: "output/x.md"\nprompt: ""').json()
    assert body["ok"] is False
    assert any("prompt" in e for e in body["errors"])


def test_non_mapping_yaml_is_rejected(client, web_env):
    _login(client)
    body = _validate(client, "- just\n- a list\n").json()
    assert body["ok"] is False
    assert body["errors"]


def test_warnings_do_not_block(client, web_env):
    _login(client)
    body = _validate(client, VALID.replace('name: "Test Job"\n', "")).json()
    assert body["ok"] is True
    assert any("name" in w for w in body["warnings"])


def test_validation_does_not_touch_the_file(client, web_env):
    """The point of preflight: nothing is written."""
    _login(client)
    before = client.get(f"/api/jobs/{JOB}").json()["yaml_content"]

    _validate(client, "name: 'changed'\n")

    after = client.get(f"/api/jobs/{JOB}").json()["yaml_content"]
    assert after == before


def test_unknown_job_is_404(client, web_env):
    _login(client)
    assert client.post("/api/jobs/nope/missing/validate",
                       json={"yaml_content": VALID},
                       headers=_headers(client)).status_code == 404


def test_viewers_cannot_validate(client, web_env):
    _login(client, "viewer", "viewer-pass-123")
    assert client.post(f"/api/jobs/{JOB}/validate",
                       json={"yaml_content": VALID},
                       headers=_headers(client)).status_code == 403


def test_editors_can_validate(client, web_env):
    _login(client, "editor", "editor-pass-123")
    assert client.post(f"/api/jobs/{JOB}/validate",
                       json={"yaml_content": VALID},
                       headers=_headers(client)).status_code == 200
