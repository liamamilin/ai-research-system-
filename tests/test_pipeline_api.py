"""Pipeline rounds API: artifact listing and raw JSON downloads."""

from __future__ import annotations

import json

from core import artifacts


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


def _seed_damaged_round(web_env) -> None:
    """A round that finished 10/10 and still lost its watchlist."""
    tmp_path, _ = web_env
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / "2026-01-03"
    round_dir.mkdir(parents=True)
    body = ("# 报告\n\n## 9. Watchlist\n\n"
            + "x" * 5000
            + "\n1. **该被解析出来的观察项**：值得盯（https://a.test/x ）。\n")
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(body, encoding="utf-8")
    (round_dir / artifacts.WATCHLIST_FILE).write_text(json.dumps({
        "items": [],
        "warnings": ["watchlist: P9 文档中未解析出任何观察项（检查章节标题与表格结构）"],
    }, ensure_ascii=False), encoding="utf-8")


def test_an_empty_watchlist_is_flagged_in_the_round_list(client, web_env):
    """A 285-byte watchlist used to look exactly like a 10 KB one.

    Nothing in the list said the round was incomplete, and the warning that
    knew better was buried inside the JSON the page offered to download.
    """
    _seed_damaged_round(web_env)
    _login(client)
    rounds = {x["date"]: x for x in client.get("/api/pipeline/rounds").json()["rounds"]}
    watch = next(a for a in rounds["2026-01-03"]["artifacts"]
                 if a["name"] == artifacts.WATCHLIST_FILE)
    assert watch.get("empty") is True
    assert any("很可能未能解析" in w for w in watch["warnings"]), watch


def test_round_detail_reports_artifact_warnings(client, web_env):
    _seed_damaged_round(web_env)
    _login(client)
    payload = client.get("/api/pipeline/rounds/2026-01-03").json()
    assert payload["warnings"], payload
    assert any("重新解析" in w for w in payload["warnings"]), payload["warnings"]


def test_the_list_does_not_pay_for_the_warning_reparse(client, web_env):
    """Warnings on the list come from the small JSON files only.

    The detail endpoint re-reads the round's documents, which is megabytes per
    round; doing that for every row of the list would make the page unusable.
    """
    _seed_damaged_round(web_env)
    _login(client)
    for row in client.get("/api/pipeline/rounds").json()["rounds"]:
        assert "warnings" not in row, row


def test_a_healthy_artifact_is_not_flagged(client, web_env):
    _seed_round(web_env)
    _login(client)
    rounds = {x["date"]: x for x in client.get("/api/pipeline/rounds").json()["rounds"]}
    action = rounds["2026-01-02"]["artifacts"][0]
    # Emptiness alone is not a defect -- a round can genuinely have no actions,
    # and the page flags on warnings, not on size.
    assert "warnings" not in action
    assert action.get("empty") is True


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
