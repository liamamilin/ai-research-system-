"""Pipeline rounds API: artifact listing and raw JSON downloads."""

from __future__ import annotations


def _login(client):
    return client.post("/api/auth/login",
                       json={"username": "admin", "password": "admin-pass-123"})


def _seed_round(web_env) -> None:
    tmp_path, _ = web_env
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / "2026-01-02"
    round_dir.mkdir(parents=True)
    (round_dir / "00_collection_plan.md").write_text("# plan", encoding="utf-8")
    (round_dir / "action_items.json").write_text('{"actions": []}', encoding="utf-8")


def test_rounds_api_lists_artifacts(client, web_env):
    _seed_round(web_env)
    _login(client)
    r = client.get("/api/pipeline/rounds")
    assert r.status_code == 200
    rounds = {x["date"]: x for x in r.json()["rounds"]}
    assert "2026-01-02" in rounds
    artifacts = rounds["2026-01-02"]["artifacts"]
    assert [a["name"] for a in artifacts] == ["action_items.json"]
    assert artifacts[0]["size"] > 0


def test_round_detail_exposes_artifacts(client, web_env):
    _seed_round(web_env)
    _login(client)
    r = client.get("/api/pipeline/rounds/2026-01-02")
    assert r.status_code == 200
    assert r.json()["artifacts"][0]["name"] == "action_items.json"


def test_raw_json_artifact_download(client, web_env):
    _seed_round(web_env)
    _login(client)
    r = client.get("/api/reports/raw",
                   params={"path": "practical_ai_intelligence/2026-01-02/action_items.json"})
    assert r.status_code == 200
    assert r.json()["content"] == '{"actions": []}'


def test_rounds_api_requires_auth(client):
    assert client.get("/api/pipeline/rounds").status_code == 401
