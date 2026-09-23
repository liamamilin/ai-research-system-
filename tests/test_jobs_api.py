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
