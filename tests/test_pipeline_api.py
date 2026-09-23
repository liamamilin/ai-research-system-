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


def _seed_diff_round(web_env, date: str, actions: list, watch: list, urls: list) -> None:
    tmp_path, _ = web_env
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / date
    round_dir.mkdir(parents=True, exist_ok=True)
    import json
    (round_dir / "action_items.json").write_text(
        json.dumps({"actions": actions, "tests": []}), encoding="utf-8")
    (round_dir / "watchlist.json").write_text(
        json.dumps({"items": watch}), encoding="utf-8")
    (round_dir / "sources.json").write_text(
        json.dumps({"sources": [{"url": u} for u in urls]}), encoding="utf-8")


def test_round_diff_defaults_to_previous_round(client, web_env):
    _seed_diff_round(web_env, "2026-01-01",
                     [{"action": "旧行动"}], [], ["https://old.test/x"])
    _seed_diff_round(web_env, "2026-01-08",
                     [{"action": "新行动"}, {"action": "旧行动"}],
                     [{"topic": "新议题"}], ["https://new.test/y"])
    _login(client)

    r = client.get("/api/pipeline/rounds/2026-01-08/diff")
    assert r.status_code == 200
    diff = r.json()
    assert diff["from"] == "2026-01-01" and diff["to"] == "2026-01-08"
    assert [a["action"] for a in diff["actions"]["added"]] == ["新行动"]
    assert diff["actions"]["removed"] == []
    assert [w["topic"] for w in diff["watchlist"]["added"]] == ["新议题"]
    assert diff["sources"]["added"] == ["https://new.test/y"]
    assert diff["sources"]["new_domains"] == ["new.test"]


def test_round_diff_explicit_against(client, web_env):
    _seed_diff_round(web_env, "2026-01-01", [{"action": "A"}], [], [])
    _seed_diff_round(web_env, "2026-01-04", [{"action": "B"}], [], [])
    _seed_diff_round(web_env, "2026-01-08", [{"action": "C"}], [], [])
    _login(client)

    r = client.get("/api/pipeline/rounds/2026-01-08/diff",
                   params={"against": "2026-01-04"})
    assert r.status_code == 200
    assert r.json()["from"] == "2026-01-04"
    assert [a["action"] for a in r.json()["actions"]["added"]] == ["C"]


def test_round_diff_errors(client, web_env):
    _seed_diff_round(web_env, "2026-01-08", [], [], [])
    _login(client)

    assert client.get("/api/pipeline/rounds/2026-01-08/diff").status_code == 200
    assert client.get("/api/pipeline/rounds/2099-01-01/diff").status_code == 404
    assert client.get("/api/pipeline/rounds/2026-01-08/diff",
                      params={"against": "bad"}).status_code == 422
    assert client.get("/api/pipeline/rounds/bad/diff").status_code == 422


P9_SAMPLE = """# Executive Synthesis

## 2. 立即行动（Immediate Actions）

| 行动 | 优先级 |
|---|---|
| 迁移模型 | P0 |

## 9. 观察清单（Watchlist）

| 议题 | 观察点 |
|---|---|
| GPT-5.5 退役 | 是否强制切换 |
"""


def test_round_export_syncs_tracking(web_env):
    tmp_path, _ = web_env
    (tmp_path / "config" / "system.yaml").write_text(
        f"defaults:\n  output_dir: \"{tmp_path / 'output'}\"\n", encoding="utf-8")
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / "2026-01-02"
    round_dir.mkdir(parents=True)
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(
        P9_SAMPLE, encoding="utf-8")

    from web.runner import pipeline
    pipeline._export_round_artifacts({"date": "2026-01-02"}, str(tmp_path / "config"))

    from core import tracking
    tracking.use_state_dir(str(tmp_path / "state"))
    items = tracking.list_items()
    assert {i["kind"] for i in items} == {"action", "watch"}
    assert any("迁移模型" in i["text"] for i in items)
    assert any("GPT-5.5 退役" in i["text"] for i in items)
