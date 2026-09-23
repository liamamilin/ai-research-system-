"""Durable run logs: log_store writes/rotation plus the history endpoint."""

from __future__ import annotations

import json
import os

import pytest

from web.runner import log_store


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


@pytest.fixture()
def logs_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    return str(d)


def test_append_and_read_single_run(logs_dir):
    log_store.append_event(logs_dir, "research/a", "run1", {"type": "log", "message": "hi"})
    log_store.append_event(logs_dir, "research/a", "run1", {"type": "status", "status": "success"})

    runs, events = log_store.read_events(logs_dir, "research/a")
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run1"
    assert runs[0]["status"] == "success"
    assert runs[0]["events"] == 2
    assert runs[0]["started_at"] and runs[0]["finished_at"]
    assert [e["event"]["type"] for e in events] == ["log", "status"]


def test_read_defaults_to_latest_run(logs_dir):
    for i in range(2):
        rid = f"run{i}"
        log_store.append_event(logs_dir, "j", rid, {"type": "log", "message": rid})
        log_store.append_event(logs_dir, "j", rid, {"type": "status", "status": "failed" if i == 0 else "success"})
    log_store.append_event(logs_dir, "j", "run2", {"type": "log", "message": "later"})
    log_store.append_event(logs_dir, "j", "run2", {"type": "status", "status": "success"})

    runs, events = log_store.read_events(logs_dir, "j")
    assert [r["run_id"] for r in runs] == ["run2", "run1", "run0"]
    assert runs[0]["status"] == "success"
    assert [e["event"].get("message") for e in events] == ["later", None]

    _, first = log_store.read_events(logs_dir, "j", run_id="run0")
    assert len(first) == 2
    assert first[0]["event"]["message"] == "run0"


def test_run_without_terminal_event_is_unknown(logs_dir):
    """A crashed or interrupted run has no status event; report it as unknown."""
    log_store.append_event(logs_dir, "j", "run-x", {"type": "log", "message": "start"})
    log_store.append_event(logs_dir, "j", "run-x", {"type": "log", "message": "still going"})
    runs, _events = log_store.read_events(logs_dir, "j")
    assert runs[0]["status"] == "unknown"
    assert runs[0]["finished_at"] == ""


def test_read_missing_file_returns_empty(logs_dir):
    assert log_store.read_events(logs_dir, "nope") == ([], [])


def test_read_skips_corrupt_lines(logs_dir):
    log_store.append_event(logs_dir, "j", "r1", {"type": "log", "message": "ok"})
    path = log_store.job_log_path(logs_dir, "j")
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not json\n\n")
    runs, events = log_store.read_events(logs_dir, "j")
    assert len(runs) == 1 and len(events) == 1


def test_limit_keeps_newest_events(logs_dir):
    for i in range(50):
        log_store.append_event(logs_dir, "j", "r1", {"type": "log", "message": str(i)})
    _, events = log_store.read_events(logs_dir, "j", limit=10)
    assert len(events) == 10
    assert events[-1]["event"]["message"] == "49"


def test_path_traversal_in_job_name_is_neutralized(logs_dir):
    path = log_store.job_log_path(logs_dir, "../../etc/passwd")
    assert os.path.dirname(os.path.abspath(path)).startswith(os.path.abspath(logs_dir))
    assert ".." not in os.path.basename(path)


def test_rotation_keeps_file_bounded(logs_dir, monkeypatch):
    monkeypatch.setattr(log_store, "MAX_LOG_BYTES", 400)
    for i in range(30):
        log_store.append_event(logs_dir, "j", "r1", {"type": "log", "message": "x" * 50})
    path = log_store.job_log_path(logs_dir, "j")
    assert os.path.getsize(path) < 400 * 2
    runs, events = log_store.read_events(logs_dir, "j")
    assert runs and events


def _make_job(web_env, name: str = "research/a") -> str:
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    (jobs / "research").mkdir(parents=True, exist_ok=True)
    (jobs / (name + ".yaml")).write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    return str(tmp_path)


def test_endpoint_returns_persisted_logs(client, web_env):
    tmp_path, _ = web_env
    _make_job(web_env)
    _login(client)
    log_store.append_event(str(tmp_path / "logs"), "research/a", "run-x",
                           {"type": "log", "message": "persisted line"})
    log_store.append_event(str(tmp_path / "logs"), "research/a", "run-x",
                           {"type": "status", "status": "success"})

    r = client.get("/api/jobs/research/a/logs")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["run"]["run_id"] == "run-x"
    assert body["run"]["status"] == "success"
    assert len(body["events"]) == 2
    assert body["live"] is False


def test_endpoint_unknown_job_is_404(client, web_env):
    _login(client)
    r = client.get("/api/jobs/research/ghost/logs")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_endpoint_requires_auth(client, web_env):
    assert client.get("/api/jobs/research/a/logs").status_code == 401


def test_endpoint_limit_is_capped(client, web_env):
    _make_job(web_env)
    _login(client)
    r = client.get("/api/jobs/research/a/logs?limit=999999")
    assert r.status_code == 200
    assert isinstance(r.json()["events"], list)


def test_persisted_lines_are_valid_jsonl(logs_dir):
    log_store.append_event(logs_dir, "j", "r1", {"type": "log", "message": "中文内容"})
    path = log_store.job_log_path(logs_dir, "j")
    with open(path, encoding="utf-8") as f:
        record = json.loads(f.readline())
    assert record["event"]["message"] == "中文内容"
    assert record["run_id"] == "r1"
    assert record["ts"]
