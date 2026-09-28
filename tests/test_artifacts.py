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
    parsed = artifacts.parse_watchlist(P9)
    items = parsed["items"]
    assert parsed["warnings"] == []
    assert len(items) == 1
    assert items[0]["topic"] == "GPT-5.5 退役"
    assert items[0]["watch_point"] == "是否强制切换"
    assert items[0]["trigger"] == "退役日报错"
    assert "https://openai.com/products/release-notes/" in items[0]["evidence"]


def test_the_watchlist_columns_the_prompt_asks_for_are_all_known():
    """The prompt and the parser must agree on the column names.

    Section 9 of the P9 output spec now says "Table: topic, trigger signal, why
    watch it, check-by date, source URL". Naming columns the alias table has
    never heard of produces a table full of unrecognized-header warnings, which
    is how the first 2026-09-28 round shipped 13 items with two of the five
    columns dropped.
    """
    table = """# 报告

## 9. Watchlist

| topic | trigger signal | why watch it | check-by date | source URL |
| --- | --- | --- | --- | --- |
| Claude Sonnet 5.5 是否上线 | 官方定价页出现该型号 | 会挤压中端位 | 2026-10-02 | https://a.test/leak |
"""
    parsed = artifacts.parse_watchlist(table)
    assert parsed["warnings"] == [], parsed["warnings"]
    item = parsed["items"][0]
    assert item["topic"] == "Claude Sonnet 5.5 是否上线"
    assert item["trigger"].startswith("官方定价页")
    assert item["watch_rationale"] == "会挤压中端位"
    assert item["deadline"] == "2026-10-02"
    assert item["evidence"] == "https://a.test/leak"


def test_a_chinese_annotation_does_not_become_part_of_the_url():
    """`https://host/path（2026-09-22 发布）` is one citation, not one plus junk.

    The extractor excluded the full-width closing bracket but not the opening
    one, so the annotation was swallowed and the citation could never match
    anything the run had retrieved. On the 2026-09-28 round that turned 12 of
    P6's 21 "fabricated" URLs into phantoms and held the infrastructure radar
    at 56% coverage, below the 60% gate, while stages that never used that
    citation style sat at 86-100%.
    """
    from core.provenance import extract_urls

    assert extract_urls("见 https://x.test/a（2026-09-22 发布）。") == \
        ["https://x.test/a"]
    assert extract_urls("https://x.test/b（公告发布）") == ["https://x.test/b"]
    assert extract_urls("https://x.test/c（未标注）") == ["https://x.test/c"]


def test_both_url_patterns_are_the_same_pattern():
    """Two copies had drifted, each missing a bracket the other had.

    The agent's pattern and the citation check's pattern must be one, or a URL
    the agent remembered and the same URL the report cites can be spelled
    differently and fail to match.
    """
    from core import provenance
    from core.research import _URL_RE

    assert _URL_RE is provenance.URL_RE


def test_a_watchlist_written_as_a_list_is_still_read():
    """The one section with no `Table:` spec got written as a list.

    `## 9. Watchlist` is the only output section in the P9 prompt that never
    said "Table:", so the model used a numbered list -- and a table-only parser
    returned nothing. The 2026-09-26 round produced an empty watchlist.json of
    285 bytes next to 11 perfectly good items in the document.
    """
    listed = """# Executive Synthesis

## 9. Watchlist

**窗口内、需在未来 1–2 周跟进**
1. **OpenAI 越权事件的监管后果**：澳大利亚已启动紧急审查（https://a.test/review ，2026-09-24）；后续可能出台强制披露时限。
2. **SWE-Bench Pro V2 的 HARD-51 子集**：是否成为行业默认难集（https://b.test/bench ）。
3. 没有粗体的条目也应被收进来，https://c.test/plain
"""
    parsed = artifacts.parse_watchlist(listed)
    items = parsed["items"]
    assert parsed["warnings"] == [], parsed["warnings"]
    assert len(items) == 3, items
    assert items[0]["topic"] == "OpenAI 越权事件的监管后果"
    assert items[0]["evidence"] == "https://a.test/review"
    # The brackets that wrapped the URL must not be left dangling or empty.
    point = items[0]["watch_point"]
    assert "（）" not in point and "( )" not in point
    assert "，2026-09-24" not in point, point
    assert "澳大利亚已启动紧急审查" in point
    assert items[1]["topic"] == "SWE-Bench Pro V2 的 HARD-51 子集"
    assert items[2]["topic"], items[2]


def test_a_table_watchlist_still_wins_over_the_list_fallback():
    """A prompt that does specify a table must not be re-parsed as a list."""
    parsed = artifacts.parse_watchlist(P9)
    assert parsed["warnings"] == []
    assert len(parsed["items"]) == 1
    assert parsed["items"][0]["topic"] == "GPT-5.5 退役"


def test_an_empty_stored_watchlist_does_not_outrank_its_own_document(tmp_path):
    """Every round written by the old parser has an empty watchlist.json on disk
    forever, and it used to win over the document it was derived from."""
    round_dir = tmp_path / "2026-01-02"
    round_dir.mkdir()
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(
        "# 报告\n\n## 9. Watchlist\n\n"
        "1. **应该被解析出来的观察项**：值得盯（https://a.test/x ）。\n",
        encoding="utf-8",
    )
    (round_dir / artifacts.WATCHLIST_FILE).write_text(json.dumps({
        "schema_version": 2,
        "items": [],
        "warnings": ["watchlist: P9 文档中未解析出任何观察项（检查章节标题与表格结构）"],
    }, ensure_ascii=False), encoding="utf-8")

    payloads = artifacts.load_round_payloads(str(round_dir))
    items = payloads["watchlist"]["items"]
    assert len(items) == 1, items
    assert items[0]["topic"] == "应该被解析出来的观察项"
    # The discarded file's own "parsed nothing" claim is exactly what we just
    # disproved, so it must not survive alongside the items it contradicts.
    joined = " ".join(payloads["watchlist"]["warnings"])
    assert "未解析出任何观察项" not in joined, joined
    assert "重新解析" in joined


def test_a_genuinely_empty_watchlist_is_left_alone(tmp_path):
    """No items in the file and none in the document is a quiet round, not a bug."""
    round_dir = tmp_path / "2026-01-03"
    round_dir.mkdir()
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(
        "# 报告\n\n## 9. Watchlist\n\n本期无需观察项。\n", encoding="utf-8")
    (round_dir / artifacts.WATCHLIST_FILE).write_text(
        json.dumps({"items": []}, ensure_ascii=False), encoding="utf-8")

    payloads = artifacts.load_round_payloads(str(round_dir))
    assert payloads["watchlist"]["items"] == []
    assert "重新解析" not in " ".join(payloads["watchlist"]["warnings"])


def test_markdown_presentation_is_stripped_from_structured_fields():
    """These fields are data, not prose to be rendered.

    40 of 120 fields in the 2026-09-26 round carried `**bold**` and 5 carried
    backticks. `priority` arrived as `**P0**`, and the UI styles priority by an
    exact match on "P0" -- so all 23 top-priority actions rendered in the muted
    grey of a P2. That is the one signal on the panel that must not lie.
    """
    listed = """# 报告

## 2. Immediate Actions

| action | priority |
|---|---|
| **A1. 冻结 prompt 前缀**：`cache` 命中率决定成本 | **P0** |
| A2. 成本模型改三维 | P1 |

## 3. Test This Week

| test | priority |
|---|---|
| **T1. 缓存命中率基准**：同任务跑 5 次 | **P0** |
"""
    parsed = artifacts.parse_action_items(listed)
    assert parsed["warnings"] == [], parsed["warnings"]

    priorities = {a["priority"] for a in parsed["actions"]} | {
        t["priority"] for t in parsed["tests"]}
    assert priorities == {"P0", "P1"}, priorities
    assert "**" not in json.dumps(parsed, ensure_ascii=False)

    first = parsed["actions"][0]
    assert first["action"].startswith("A1. 冻结 prompt 前缀")
    assert "cache" in first["action"] and "`" not in first["action"]


def test_underscore_emphasis_is_stripped_but_snake_case_is_not():
    """"__x__" is emphasis; "max_rounds" is a variable name."""
    from core.artifacts import _clean_cell

    assert _clean_cell("__重点__：见 max_rounds 与 token_budget") == \
        "重点：见 max_rounds 与 token_budget"


def test_lone_asterisks_are_left_alone():
    """A single * is arithmetic far more often than italics, and stripping it
    would corrupt values like "3* 和 5* 的模型"."""
    from core.artifacts import _clean_cell

    assert _clean_cell("3* 模型") == "3* 模型"
    assert _clean_cell("a * b") == "a * b"


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
