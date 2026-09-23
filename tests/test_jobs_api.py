"""Tests for the job categories endpoint."""

from __future__ import annotations


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _write_jobs(web_env):
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    (jobs / "research").mkdir(parents=True)
    (jobs / "monitoring").mkdir(parents=True)
    (jobs / "research" / "a.yaml").write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    (jobs / "research" / "b.yaml").write_text('name: "B"\nprompt: "x"\n', encoding="utf-8")
    (jobs / "monitoring" / "c.yaml").write_text('name: "C"\nprompt: "x"\n', encoding="utf-8")
    (jobs / "root.yaml").write_text('name: "Root"\nprompt: "x"\n', encoding="utf-8")
    (jobs / "_templates").mkdir()
    (jobs / "_templates" / "t.yaml").write_text('name: "T"\nprompt: "x"\n', encoding="utf-8")
    return tmp_path


def test_requires_auth(client, web_env):
    assert client.get("/api/jobs/categories").status_code == 401


def test_lists_categories_with_counts(client, web_env):
    _write_jobs(web_env)
    _login(client)
    r = client.get("/api/jobs/categories")
    assert r.status_code == 200
    cats = {c["name"]: c["count"] for c in r.json()["categories"]}
    assert cats == {"monitoring": 1, "research": 2}


def test_empty_dir_returns_empty(client, web_env):
    _login(client)
    assert client.get("/api/jobs/categories").json()["categories"] == []


def test_stream_returns_idle_for_existing_job_without_run(client, web_env):
    """An existing job with no active run must answer 'idle', not 404.

    A 404 makes EventSource retry in a loop and leaves the log panel stuck on
    "waiting for logs" after the user starts a run.
    """
    _write_jobs(web_env)
    _login(client)
    r = client.get("/api/jobs/research/a/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert '"status": "idle"' in body or '"status":"idle"' in body


def test_stream_404_for_unknown_job(client, web_env):
    _write_jobs(web_env)
    _login(client)
    r = client.get("/api/jobs/research/does-not-exist/stream")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "no_task"
