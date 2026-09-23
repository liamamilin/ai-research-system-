"""Tests for structured round artifacts (actions / watchlist / sources)."""

from __future__ import annotations

import json
import os

from core import artifacts

P9 = """# 执行综合与行动计划（Executive Synthesis and Action Plan）

## 1. 执行摘要

- 判断一

## 2. 立即行动（Immediate Actions）

| 行动 | 为何现在 | 关联雷达 | 预期收益 | 工作量 | 优先级 |
|---|---|---|---|---|---|
| 迁移模型 | 退役临近 | P1、P2 | 避免停摆 | 低 | P0 |
| 加引用闸门 | 降低幻觉 | P5 | 可审计 | 中 | P1 |

## 3. 本周测试（Test This Week）

| 测试 | 方法 | 成功标准 | 成本 | 风险 | 优先级 |
|---|---|---|---|---|---|
| T1 换壳对照 | 固定模型跑两壳 | 差距 >2 倍则切换 | 低 | 样本小 | P0 |

## 4. 模型路由影响（Model Routing Implications）

| 档位 | 选择 |
|---|---|
| 廉价 | DeepSeek |

## 9. 观察清单（Watchlist）

| 议题 | 观察点 | 触发信号 | 依据 |
|---|---|---|---|
| GPT-5.5 退役 | 是否强制切换 | 退役日报错 | 2026-09-14，https://openai.com/products/release-notes/ |

## 10. 被忽略的主题（Ignored Themes）

| 主题 | 理由 |
|---|---|
| 榜单搬运 | 无差异化 |
"""


def test_parse_action_items_extracts_rows():
    parsed = artifacts.parse_action_items(P9)
    assert len(parsed["actions"]) == 2
    first = parsed["actions"][0]
    assert first["action"] == "迁移模型"
    assert first["why_now"] == "退役临近"
    assert first["related_radars"] == "P1、P2"
    assert first["effort"] == "低"
    assert first["priority"] == "P0"
    assert len(parsed["tests"]) == 1
    assert parsed["tests"][0]["test"] == "T1 换壳对照"
    assert parsed["tests"][0]["success_criteria"] == "差距 >2 倍则切换"


def test_sections_do_not_leak_into_each_other():
    parsed = artifacts.parse_action_items(P9)
    actions_text = json.dumps(parsed["actions"], ensure_ascii=False)
    assert "DeepSeek" not in actions_text
    assert all(a["action"] != "榜单搬运" for a in parsed["actions"])


def test_parse_watchlist_extracts_rows():
    items = artifacts.parse_watchlist(P9)
    assert len(items) == 1
    assert items[0]["topic"] == "GPT-5.5 退役"
    assert items[0]["watch_point"] == "是否强制切换"
    assert items[0]["trigger"] == "退役日报错"
    assert "https://openai.com/products/release-notes/" in items[0]["evidence"]


def test_extract_sources_dedupes_and_strips_punctuation():
    text = (
        "见 https://a.test/x）以及 https://a.test/x ，"
        "还有 https://b.test/y。重复 https://a.test/x#frag\n"
        "非 URL: ftp://c.test/z"
    )
    urls = artifacts.extract_sources(text)
    assert urls == ["https://a.test/x", "https://b.test/y", "https://a.test/x#frag"]


def test_export_writes_all_three_files(tmp_path):
    round_dir = tmp_path / "practical_ai_intelligence" / "2026-01-02"
    round_dir.mkdir(parents=True)
    (round_dir / "00_collection_plan.md").write_text(
        "plan https://plan.test/a", encoding="utf-8")
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(
        P9, encoding="utf-8")

    written = artifacts.export_round_artifacts(str(round_dir), date="2026-01-02")
    assert written is not None

    for name in (artifacts.ACTION_FILE, artifacts.WATCHLIST_FILE, artifacts.SOURCES_FILE):
        assert (round_dir / name).is_file()

    actions = json.loads((round_dir / artifacts.ACTION_FILE).read_text(encoding="utf-8"))
    assert actions["date"] == "2026-01-02"
    assert actions["source_document"] == "09_executive_synthesis_and_actions.md"
    assert len(actions["actions"]) == 2

    watch = json.loads((round_dir / artifacts.WATCHLIST_FILE).read_text(encoding="utf-8"))
    assert len(watch["items"]) == 1

    sources = json.loads((round_dir / artifacts.SOURCES_FILE).read_text(encoding="utf-8"))
    assert sources["total_sources"] == 2
    assert sources["total_unique"] == 2
    assert set(sources["by_document"]) == {
        "00_collection_plan.md", "09_executive_synthesis_and_actions.md"}
    urls = {s["url"] for s in sources["sources"]}
    assert "https://plan.test/a" in urls
    assert "https://openai.com/products/release-notes/" in urls
    assert sources["domains"]["openai.com"] == 1


def test_export_without_p9_still_writes_sources(tmp_path):
    round_dir = tmp_path / "round"
    round_dir.mkdir()
    (round_dir / "01_model_and_pricing_radar.md").write_text(
        "见 https://a.test/1", encoding="utf-8")

    written = artifacts.export_round_artifacts(str(round_dir))
    assert written is not None
    assert written[artifacts.ACTION_FILE]["actions"] == []
    assert written[artifacts.ACTION_FILE]["source_document"] is None
    assert written[artifacts.SOURCES_FILE]["total_unique"] == 1


def _round(tmp_path, date: str, actions: list, watch: list, urls: list):
    round_dir = tmp_path / date
    round_dir.mkdir(parents=True)
    (round_dir / artifacts.ACTION_FILE).write_text(
        json.dumps({"actions": actions, "tests": []}), encoding="utf-8")
    (round_dir / artifacts.WATCHLIST_FILE).write_text(
        json.dumps({"items": watch}), encoding="utf-8")
    (round_dir / artifacts.SOURCES_FILE).write_text(
        json.dumps({"sources": [{"url": u} for u in urls]}), encoding="utf-8")
    return round_dir


def test_diff_rounds_detects_changes(tmp_path):
    a = _round(tmp_path, "2026-01-01",
               [{"action": "1. 迁移模型", "priority": "P0"},
                {"action": "旧行动"}],
               [{"topic": "旧议题"}],
               ["https://a.test/1", "https://old.test/x"])
    b = _round(tmp_path, "2026-01-08",
               [{"action": "迁移模型", "priority": "P0"},
                {"action": "新行动", "priority": "P1"}],
               [{"topic": "新议题"}, {"topic": "旧议题"}],
               ["https://a.test/1", "https://new.test/y"])

    diff = artifacts.diff_rounds(str(a), str(b))
    assert diff["from"] == "2026-01-01" and diff["to"] == "2026-01-08"
    assert [x["action"] for x in diff["actions"]["added"]] == ["新行动"]
    assert [x["action"] for x in diff["actions"]["removed"]] == ["旧行动"]
    assert [x["action"] for x in diff["actions"]["persisted"]] == ["迁移模型"]
    assert [x["topic"] for x in diff["watchlist"]["added"]] == ["新议题"]
    assert diff["sources"]["added"] == ["https://new.test/y"]
    assert diff["sources"]["removed"] == ["https://old.test/x"]
    assert diff["sources"]["new_domains"] == ["new.test"]
    assert diff["counts"]["actions_added"] == 1


def test_diff_rounds_falls_back_to_markdown(tmp_path):
    a = tmp_path / "2026-01-01"
    a.mkdir()
    (a / "09_executive_synthesis_and_actions.md").write_text(
        "## 2. 立即行动（Immediate Actions）\n\n"
        "| 行动 | 优先级 |\n|---|---|\n| 迁移模型 | P0 |\n\n"
        "## 9. 观察清单（Watchlist）\n\n"
        "| 议题 | 观察点 |\n|---|---|\n| 旧议题 | x |\n",
        encoding="utf-8")
    b = _round(tmp_path, "2026-01-08", [{"action": "迁移模型"}], [], [])

    diff = artifacts.diff_rounds(str(a), str(b))
    assert diff["actions"]["persisted"][0]["action"] == "迁移模型"
    assert diff["watchlist"]["removed"][0]["topic"] == "旧议题"


def test_diff_rounds_missing_dir_returns_none(tmp_path):
    assert artifacts.diff_rounds(str(tmp_path / "nope"), str(tmp_path)) is None


def test_export_missing_dir_returns_none(tmp_path):
    assert artifacts.export_round_artifacts(str(tmp_path / "nope")) is None


def test_export_empty_dir_returns_none(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert artifacts.export_round_artifacts(str(empty)) is None
