"""Contract alignment between P9 artifacts and tracking.

Covers the silent data loss found on 2026-09-24 (watchlist keys drifted from
``topic`` to ``观察项``, dropping all 15 items), similarity-based dedup, and
the outcome feedback loop.
"""

from __future__ import annotations

import os

import pytest

from core import artifacts, tracking


@pytest.fixture()
def db(tmp_path, monkeypatch):
    tracking.set_db_path(str(tmp_path / "tracking.db"))
    tracking.init_db()
    yield
    tracking.set_db_path(None)


# --- header alias / contract drift -----------------------------------------


def _p9(actions_table: str = "", watch_table: str = "") -> str:
    nl = "\n"
    default_actions = nl.join([
        "| 行动 | 为何现在 | 优先级 |",
        "| --- | --- | --- |",
        "| 建立成本日历 | 涨价已落地 | P0 |",
    ])
    default_watch = nl.join([
        "| 议题 | 触发信号 | 依据 |",
        "| --- | --- | --- |",
        "| Gemini 定价 | Q4 复审 | 官方博客 |",
    ])
    return nl.join([
        "# P9 Executive Synthesis",
        "",
        "## 立即行动",
        "",
        actions_table or default_actions,
        "",
        "## 本周测试",
        "",
        nl.join([
            "| 测试 | 成功标准 | 优先级 |",
            "| --- | --- | --- |",
            "| 灰度 3 个任务 | 错误率不上升 | P1 |",
        ]),
        "",
        "## 观察清单",
        "",
        watch_table or default_watch,
        "",
    ])


def test_parse_uses_canonical_keys_for_legacy_headers():
    parsed = artifacts.parse_watchlist(_p9())
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["topic"] == "Gemini 定价"
    assert parsed["items"][0]["trigger"] == "Q4 复审"
    assert parsed["warnings"] == []


def test_parse_maps_drifted_chinese_headers():
    text = _p9(watch_table=(
        "| 观察项 | 为什么观察 | 触发条件_/_复审时点 |\n"
        "| --- | --- | --- |\n"
        "| **DigitalOcean Managed Agents 的 GA 与定价** | 当前为公有预览，SLA 未验证 | Q1 复审；先借用数据模型 |"
    ))
    parsed = artifacts.parse_watchlist(text)
    assert len(parsed["items"]) == 1
    item = parsed["items"][0]
    assert item["topic"].startswith("**DigitalOcean")
    assert "SLA" in item["watch_rationale"]
    assert "Q1 复审" in item["trigger"]
    assert parsed["warnings"] == []


def test_parse_maps_alternative_action_headers():
    text = _p9(actions_table=(
        "| 行动项 | 为什么是现在 | 负责人 | 验收标准 | 时限 | 优先级 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        "| 迁移到新缓存 | 成本翻倍 | 平台组 | 账单下降 30% | 两周 | P0 |"
    ))
    parsed = artifacts.parse_action_items(text)
    assert len(parsed["actions"]) == 1
    action = parsed["actions"][0]
    assert action["action"] == "迁移到新缓存"
    assert action["why_now"] == "成本翻倍"
    assert action["owner_role"] == "平台组"
    assert action["acceptance"] == "账单下降 30%"
    assert action["deadline"] == "两周"
    assert action["priority"] == "P0"


def test_unrecognized_header_is_reported_not_dropped():
    text = _p9(actions_table=(
        "| 行动 | 热度指标 | 优先级 |\n"
        "| --- | --- | --- |\n"
        "| 观察定价页 | +180% | P1 |"
    ))
    parsed = artifacts.parse_action_items(text)
    assert len(parsed["actions"]) == 1
    assert parsed["actions"][0]["col_2"] == "+180%"
    assert any("热度指标" in w for w in parsed["warnings"])


def test_empty_primary_column_falls_back_and_warns():
    text = _p9(actions_table=(
        "| 说明 | 优先级 |\n"
        "| --- | --- |\n"
        "| 评估是否自建推理集群 | P2 |"
    ))
    parsed = artifacts.parse_action_items(text)
    assert len(parsed["actions"]) == 1
    assert parsed["actions"][0]["action"] == "评估是否自建推理集群"
    assert any("缺少 'action'" in w for w in parsed["warnings"])


def test_missing_section_is_reported():
    text = "# P9\n\n## 立即行动\n\n没有任何表格。\n"
    parsed = artifacts.parse_action_items(text)
    assert parsed["actions"] == [] and parsed["tests"] == []
    assert any("未解析出任何行动" in w for w in parsed["warnings"])


def test_export_persists_schema_version_and_warnings(tmp_path):
    round_dir = tmp_path / "2026-09-25"
    round_dir.mkdir()
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(_p9(), encoding="utf-8")
    (round_dir / "01_model_and_pricing_radar.md").write_text("见 https://example.com/a\n", encoding="utf-8")

    written = artifacts.export_round_artifacts(str(round_dir), date="2026-09-25")
    assert written[artifacts.ACTION_FILE]["schema_version"] == artifacts.SCHEMA_VERSION
    assert "warnings" in written[artifacts.WATCHLIST_FILE]
    assert len(written[artifacts.ACTION_FILE]["actions"]) == 1


def test_load_round_payloads_normalizes_existing_json(tmp_path):
    round_dir = tmp_path / "2026-09-24"
    round_dir.mkdir()
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(_p9(), encoding="utf-8")
    (round_dir / artifacts.WATCHLIST_FILE).write_text(
        '{"items": [{"观察项": "X 定价", "触发条件_/_复审时点": "Q4"}]}',
        encoding="utf-8",
    )
    payload = artifacts.load_round_payloads(str(round_dir))
    items = payload["watchlist"]["items"]
    assert len(items) == 1
    assert items[0]["topic"] == "X 定价"


# --- tracking ingestion -----------------------------------------------------


def test_sync_round_accepts_canonical_payload(db):
    counts = tracking.sync_round(
        "2026-09-25",
        actions=[{"action": "建立成本日历", "priority": "P0"}],
        tests=[],
        watchlist=[{"topic": "Gemini 定价", "trigger": "Q4"}],
    )
    assert counts["new"] == 2
    assert tracking.get_item(tracking.item_id("action", "建立成本日历"))["priority"] == "P0"


def test_sync_round_accepts_legacy_chinese_keys(db):
    counts = tracking.sync_round(
        "2026-09-25",
        watchlist=[{"观察项": "Gemini 定价", "触发条件_/_复审时点": "Q4"}],
    )
    assert counts["new"] == 1
    items = tracking.list_items(kind="watch")
    assert items[0]["text"] == "Gemini 定价"


# --- similarity dedup -------------------------------------------------------


def test_similarity_merges_reworded_action(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "建立模型成本日历，跟踪每 token 支出"}])
    counts = tracking.sync_round("2026-09-25", actions=[{"action": "建立成本日历并跟踪每 token 花费"}])
    assert counts["new"] == 0
    assert counts["merged"] == 1
    items = tracking.list_items(kind="action")
    assert len(items) == 1
    assert items[0]["times_seen"] == 2
    assert "相似条目再次出现" in items[0]["note"]


def test_similarity_merges_english_variants(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "Add prompt caching for system prefix"}])
    counts = tracking.sync_round("2026-09-25", actions=[{"action": "Add prompt caching to the system prefix"}])
    assert counts["merged"] == 1
    assert len(tracking.list_items(kind="action")) == 1


def test_similarity_keeps_unrelated_actions_separate(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "建立模型成本日历"}])
    counts = tracking.sync_round("2026-09-25", actions=[{"action": "把推理迁移到自建集群"}])
    assert counts["new"] == 1
    assert counts["merged"] == 0
    assert len(tracking.list_items(kind="action")) == 2


def test_dedup_does_not_cross_kinds(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "评估提示缓存收益"}])
    counts = tracking.sync_round("2026-09-25", tests=[{"test": "评估提示缓存收益"}])
    assert counts["new"] == 1
    assert len(tracking.list_items()) == 2


def test_similar_item_reappearing_after_done_keeps_status_and_warns(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "建立模型成本日历，跟踪每 token 支出"}])
    iid = tracking.item_id("action", "建立模型成本日历，跟踪每 token 支出")
    tracking.set_status(iid, "done", note="已上线看板")

    counts = tracking.sync_round("2026-09-25", actions=[{"action": "建立成本日历并跟踪每 token 花费"}])
    assert counts["merged"] == 1
    assert any("done" in w for w in counts["warnings"])
    item = tracking.get_item(iid)
    assert item["status"] == "done"
    assert item["note"].startswith("已上线看板")


# --- outcome feedback loop --------------------------------------------------


def test_set_status_records_outcome(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "建立成本日历"}])
    iid = tracking.item_id("action", "建立成本日历")
    assert tracking.set_status(iid, "done", note="已上线", decided_by="admin")

    outcomes = tracking.recent_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["text"] == "建立成本日历"
    assert outcomes[0]["status"] == "done"
    assert outcomes[0]["note"] == "已上线"
    assert outcomes[0]["decided_by"] == "admin"


def test_set_status_unknown_id_does_not_record(db):
    assert tracking.set_status("nope", "done") is False
    assert tracking.recent_outcomes() == []


def test_repeated_same_status_does_not_duplicate_outcome(db):
    tracking.sync_round("2026-09-20", actions=[{"action": "建立成本日历"}])
    iid = tracking.item_id("action", "建立成本日历")
    tracking.set_status(iid, "done")
    tracking.set_status(iid, "done")
    assert len(tracking.recent_outcomes()) == 1


def test_format_outcomes_renders_prompt_block(db):
    assert tracking.format_outcomes() == ""
    tracking.sync_round("2026-09-20", actions=[{"action": "建立成本日历"}])
    tracking.set_status(tracking.item_id("action", "建立成本日历"), "done", note="已上线")
    block = tracking.format_outcomes()
    assert "上一轮行动结果" in block
    assert "建立成本日历" in block
    assert "已上线" in block


def test_format_outcomes_respects_days_window(db):
    with tracking.connect() as conn:
        conn.execute(
            "INSERT INTO outcomes (item_id, kind, text, status, note, decided_at, decided_by)"
            " VALUES (?,?,?,?,?,?,?)",
            ("old", "action", "很久以前的决定", "done", "", "2020-01-01T00:00:00+0800", "admin"),
        )
    assert tracking.recent_outcomes(days=30) == []
    assert tracking.format_outcomes(days=30) == ""
    assert len(tracking.recent_outcomes(days=3650)) == 1


def test_engine_prompt_injects_outcomes(db, monkeypatch):
    from core.engine import ResearchEngine

    tracking.sync_round("2026-09-20", actions=[{"action": "建立成本日历"}])
    tracking.set_status(tracking.item_id("action", "建立成本日历"), "done", note="已上线")

    engine = ResearchEngine.__new__(ResearchEngine)
    engine._date_override = None
    prompt = engine._build_prompt({
        "name": "测试",
        "keywords": ["a"],
        "prompt": "回顾 {recent_outcomes} 与 {date_1d_ago} ~ {date}",
    })
    assert "建立成本日历" in prompt
    assert "已上线" in prompt
    assert "{" not in prompt


def test_engine_prompt_without_outcomes_is_empty(db):
    from core.engine import ResearchEngine

    engine = ResearchEngine.__new__(ResearchEngine)
    engine._date_override = None
    prompt = engine._build_prompt({"name": "x", "prompt": "结果：{recent_outcomes}"})
    assert prompt.strip() == "结果："
