"""Tests for the job template CRUD endpoints."""

from __future__ import annotations

import os
import re


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _create(client, **over):
    return client.post("/api/jobs/templates", json=_payload(**over), headers=_csrf(client))


def _update(client, key, body):
    return client.put(f"/api/jobs/templates/{key}", json=body, headers=_csrf(client))


def _delete(client, key):
    return client.delete(f"/api/jobs/templates/{key}", headers=_csrf(client))


def _write_jobs(web_env):
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    (jobs / "research").mkdir(parents=True)
    (jobs / "research" / "a.yaml").write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    tmpl_dir = jobs / "_templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "_daily.yaml").write_text(
        'name: "<Topic> Daily Briefing"\n'
        'label: "每日情报速报"\n'
        'category: "monitoring"\n'
        'description: "24h 速报"\n'
        'enabled: false\n'
        'keywords: [ai, agent]\n'
        'language: "zh"\n'
        'runtime:\n  timeout_seconds: 1800\n'
        'prompt: |\n  简报 {name}，关键词 {keywords}，窗口 {date_7d_ago} ~ {date}。\n'
        'output: "output/monitoring/{date}_{name}.md"\n',
        encoding="utf-8",
    )
    return tmp_path


def _payload(**over):
    data = {
        "key": "my-template",
        "label": "我的模板",
        "category": "analysis",
        "description": "自建模板",
        "prompt": "分析 {name}，关注 {keywords}，基准日期 {date}，语言 {language}。请给出证据与结论。",
        "keywords": ["ai", "rag"],
        "language": "zh",
        "output": "output/analysis/{date}_{name}.md",
        "timeout_seconds": 1800,
        "schedule": {"type": "weekly", "time": "08:00", "timezone": "Asia/Shanghai"},
    }
    data.update(over)
    return data


def _read_yaml(path: str) -> dict:
    from web.services import yaml_io
    with open(path, encoding="utf-8") as f:
        return yaml_io.parse_yaml(f.read())


def test_requires_auth(client, web_env):
    assert client.get("/api/jobs/templates").status_code == 401
    assert client.post("/api/jobs/templates", json=_payload()).status_code == 401
    assert client.put("/api/jobs/templates/x", json=_payload()).status_code == 401
    assert client.delete("/api/jobs/templates/x").status_code == 401


def test_requires_editor(client, web_env):
    _write_jobs(web_env)
    _login(client, "viewer", "viewer-pass-123")
    assert _create(client).status_code == 403
    assert client.get("/api/jobs/templates").status_code == 200


def test_list_exposes_metadata(client, web_env):
    _write_jobs(web_env)
    _login(client)
    r = client.get("/api/jobs/templates")
    assert r.status_code == 200
    t = r.json()["templates"][0]
    assert t["key"] == "_daily"
    assert t["label"] == "每日情报速报"
    assert t["category"] == "monitoring"
    assert t["builtin"] is True
    assert t["variables"] == ["date", "date_7d_ago", "keywords", "name"]
    assert t["updated_at"]
    assert "output/monitoring" in t["output_template"]
    assert "prompt: |" in t["content"]


def test_create_writes_yaml_file(client, web_env):
    tmp_path, _ = web_env
    _write_jobs(web_env)
    _login(client)
    r = _create(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["key"] == "my-template"
    assert body["label"] == "我的模板"
    assert body["builtin"] is False
    assert body["category"] == "analysis"

    path = os.path.join(str(tmp_path), "jobs", "_templates", "my-template.yaml")
    assert os.path.isfile(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "prompt: |" in content
    assert "enabled: false" in content

    parsed = _read_yaml(path)
    assert parsed["prompt"].strip().startswith("分析")
    assert parsed["keywords"] == ["ai", "rag"]
    assert parsed["runtime"]["timeout_seconds"] == 1800
    assert parsed["schedule"]["type"] == "weekly"


def test_create_rejects_duplicate_key(client, web_env):
    _write_jobs(web_env)
    _login(client)
    assert _create(client).status_code == 201
    r = _create(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "template_exists"


def test_create_rejects_bad_input(client, web_env):
    _write_jobs(web_env)
    _login(client)

    r = _create(client, key="../evil")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_template_key"

    assert _create(client, key="_hidden").status_code == 422
    assert _create(client, category="nope").status_code == 422

    r = _create(client, prompt="太短")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "prompt_too_short"

    r = _create(client, prompt="分析 {name} 与未知变量 {bogus_var} 的关系说明")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_variables"
    assert "bogus_var" in r.json()["error"]["message"]

    assert _create(client, output="/etc/passwd.md").status_code == 422
    assert _create(client, output="output/../x.md").status_code == 422
    assert _create(client, output="output/{unknown}.md").status_code == 422
    assert _create(client, timeout_seconds=5).status_code == 422
    assert _create(client, schedule={"type": "hourly"}).status_code == 422
    assert _create(client, schedule={"type": "daily", "time": "25:00"}).status_code == 422
    assert _create(client, label="").status_code == 422
    assert _create(client, prompt="").status_code == 422


def test_update_merges_fields(client, web_env):
    tmp_path, _ = web_env
    _write_jobs(web_env)
    _login(client)
    _create(client)

    r = _update(client, "my-template", {"description": "改过的描述", "timeout_seconds": 7200})
    assert r.status_code == 200, r.text
    assert r.json()["description"] == "改过的描述"

    path = os.path.join(str(tmp_path), "jobs", "_templates", "my-template.yaml")
    parsed = _read_yaml(path)
    assert parsed["label"] == "我的模板"
    assert parsed["category"] == "analysis"
    assert parsed["prompt"].strip().startswith("分析")
    assert parsed["runtime"]["timeout_seconds"] == 7200

    backups = list((tmp_path / "state" / "backups").glob("*my-template*"))
    assert backups

    assert _update(client, "my-template", {"prompt": "太短"}).status_code == 422
    assert _update(client, "missing-template", {"label": "x"}).status_code == 404


def test_update_builtin_allowed_but_delete_protected(client, web_env):
    _write_jobs(web_env)
    _login(client)
    r = _update(client, "_daily", {"description": "自定义速报"})
    assert r.status_code == 200
    assert r.json()["description"] == "自定义速报"

    d = _delete(client, "_daily")
    assert d.status_code == 409
    assert d.json()["error"]["code"] == "builtin_template"


def test_delete_user_template(client, web_env):
    tmp_path, _ = web_env
    _write_jobs(web_env)
    _login(client)
    _create(client)
    r = _delete(client, "my-template")
    assert r.status_code == 200
    assert r.json()["key"] == "my-template"
    assert not os.path.isfile(os.path.join(str(tmp_path), "jobs", "_templates", "my-template.yaml"))
    assert _delete(client, "my-template").status_code == 404


def test_create_job_from_template_applies_overrides(client, web_env):
    tmp_path, _ = web_env
    _write_jobs(web_env)
    _login(client)
    r = client.post("/api/jobs", json={
        "name": "AI Agent 日报",
        "template": "_daily.yaml",
        "category": "monitoring",
        "description": "我的日报",
        "language": "zh",
        "keywords": ["ai agent", "llm"],
    }, headers=_csrf(client))
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "monitoring/ai_agent_日报"

    path = os.path.join(str(tmp_path), "jobs", "monitoring", "ai_agent_日报.yaml")
    parsed = _read_yaml(path)
    assert parsed["name"] == "AI Agent 日报"
    assert parsed["description"] == "我的日报"
    assert parsed["keywords"] == ["ai agent", "llm"]
    assert parsed["enabled"] is True
    assert "label" not in parsed
    assert "category" not in parsed
    assert parsed["runtime"]["timeout_seconds"] == 1800
    assert "{name}" in parsed["prompt"]


def test_create_job_from_template_keeps_prompt_when_not_overridden(client, web_env):
    tmp_path, _ = web_env
    _write_jobs(web_env)
    _login(client)
    r = client.post("/api/jobs", json={"name": "t2", "template": "daily"}, headers=_csrf(client))
    assert r.status_code == 201, r.text
    parsed = _read_yaml(os.path.join(str(tmp_path), "jobs", "t2.yaml"))
    assert "关键词" in parsed["prompt"]
    assert parsed["enabled"] is True
    assert parsed["keywords"] == ["ai", "agent"]


def test_builtin_templates_are_valid():
    from web.services import yaml_io
    repo_templates = os.path.join(os.path.dirname(os.path.dirname(__file__)), "jobs", "_templates")
    names = sorted(f for f in os.listdir(repo_templates) if f.endswith((".yaml", ".yml")))
    assert len(names) >= 10
    allowed = {"name", "keywords", "language", "date", "date_1d_ago", "date_7d_ago",
               "time", "datetime", "recent_outcomes", "reported_events"}
    categories = {"monitoring", "research", "analysis", "practice", "actionable"}
    for fn in names:
        with open(os.path.join(repo_templates, fn), encoding="utf-8") as f:
            parsed = yaml_io.parse_yaml(f.read())
        assert parsed.get("label"), f"{fn} missing label"
        assert parsed.get("category") in categories, f"{fn} bad category"
        assert len(str(parsed.get("prompt", ""))) > 500, f"{fn} prompt too short"
        assert parsed.get("enabled") is False, f"{fn} should be disabled"
        assert str(parsed.get("output", "")).endswith(".md"), f"{fn} bad output"
        unknown = [v for v in re.findall(r"\{(\w+)\}", str(parsed.get("prompt", ""))) if v not in allowed]
        assert not unknown, f"{fn} unknown variables: {unknown}"
