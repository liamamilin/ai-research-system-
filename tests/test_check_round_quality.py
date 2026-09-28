"""The round-quality checker has to actually catch the failures it exists for.

Each case below is a bug that shipped in a round which reported success: a
watchlist that parsed to nothing, a synthesis stage that ran with no input, a
citation figure measured with a broken extractor, markdown left inside
structured fields. The unit suite covers the individual functions; this covers
the thing an operator runs after a round.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_round_quality.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_round_quality", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def checker():
    return _load()


def _seed(output_dir: Path, date: str, *, p9_body: str, watchlist=None,
          action_rows: int = 3) -> Path:
    from core.pipeline_docs import STAGE_FILE, STAGES

    round_dir = output_dir / "practical_ai_intelligence" / date
    round_dir.mkdir(parents=True)
    for key, fname, _label in STAGES:
        if key == "09_executive_synthesis_and_actions":
            (round_dir / fname).write_text(p9_body, encoding="utf-8")
        else:
            (round_dir / fname).write_text(
                f"# {key}\n\n## 1. 内容\n\n" + "实质内容。" * 400 + "\n\n"
                + "参考 https://a.test/x 与 https://b.test/y。\n" * 8,
                encoding="utf-8")
    if watchlist is not None:
        (round_dir / "watchlist.json").write_text(
            json.dumps(watchlist, ensure_ascii=False), encoding="utf-8")
    return round_dir


HEALTHY_WATCHLIST = """# 报告

## 9. Watchlist

| topic | trigger signal | why watch it | check-by date | source URL |
| --- | --- | --- | --- | --- |
| A 是否上线 | 官方公告出现 | 会挤压中端位 | 2026-10-02 | https://a.test/leak |
| B 是否降价 | 定价页更新 | 影响成本测算 | 2026-10-09 | https://b.test/price |
"""

HEALTHY_ACTIONS = """## 2. Immediate Actions

| action | priority |
|---|---|
| 冻结 prompt 前缀 | P0 |
| 成本模型改三维 | P1 |
"""


def _body(watchlist=HEALTHY_WATCHLIST, actions=HEALTHY_ACTIONS) -> str:
    # Padded to a realistic size: the "P9 is suspiciously small" gate is
    # calibrated on real reports, which run 20-50 KB.
    filler = "\n\n".join(f"## 附注 {i}\n\n" + "补充说明。" * 200
                          for i in range(8))
    return (
        "# 综合报告\n\n" + actions + "\n## 3. Test This Week\n\n"
        "| test | priority |\n| --- | --- |\n| 跑一次基准 | P0 |\n"
        "\n" + watchlist + filler
    )


def test_a_healthy_round_reports_no_problems(checker, tmp_path, monkeypatch):
    out = tmp_path / "output"
    _seed(out, "2026-01-02", p9_body=_body(),
          watchlist={"items": [{"topic": "A"}, {"topic": "B"}]})
    monkeypatch.setattr(checker, "upstream_for",
                        lambda key, job, date, root="": "上游材料" * 4000)

    problems: list[str] = []
    notes: list[str] = []
    round_dir = out / "practical_ai_intelligence" / "2026-01-02"
    checker.check_stages_exist(round_dir, problems, notes)
    checker.check_no_placeholder_survived(round_dir, problems)
    checker.check_watchlist(round_dir, problems, notes)
    assert problems == [], problems


def test_an_empty_exported_watchlist_is_caught(checker, tmp_path, monkeypatch):
    """The 2026-09-26 round: the document had 11 items and the export had none.

    The document is fine, the export is not, and the board shows the export --
    so a reader saw an empty watchlist on a round marked 10/10.
    """
    out = tmp_path / "output"
    _seed(out, "2026-01-02", p9_body=_body(),
          watchlist={"items": [], "warnings": []})
    monkeypatch.setattr(checker, "ROOT", tmp_path)

    problems: list[str] = []
    notes: list[str] = []
    round_dir = out / "practical_ai_intelligence" / "2026-01-02"
    checker.check_watchlist(round_dir, problems, notes)
    assert any("watchlist.json 为空" in p for p in problems), problems


def test_a_watchlist_with_no_sources_is_caught(checker, tmp_path, monkeypatch):
    out = tmp_path / "output"
    body = ("# 报告\n\n## 9. Watchlist\n\n"
            "1. **某主题是否上线**：需要观察。\n"
            "2. **另一主题**：暂无来源。\n")
    _seed(out, "2026-01-02", p9_body=body, watchlist={"items": [{"topic": "x"}]})
    monkeypatch.setattr(checker, "ROOT", tmp_path)

    problems: list[str] = []
    notes: list[str] = []
    checker.check_watchlist(out / "practical_ai_intelligence" / "2026-01-02",
                            problems, notes)
    assert any("没有来源 URL" in p for p in problems), problems


def test_missing_upstream_is_caught_and_not_by_substring(checker, monkeypatch):
    """The failure mode this guards: synthesis stages running with no input.

    And a trap: the documents themselves talk about unavailable inputs, so a
    naive `"不可用" in upstream` test fires on their prose. The checker has to
    match the sentinel at the start.
    """
    problems: list[str] = []
    notes: list[str] = []
    monkeypatch.setattr(checker, "upstream_for",
                        lambda key, job, date, root="": "（本轮上游文档不可用：找不到目录 x）")
    checker.check_synthesis_got_its_input("2026-01-02", problems, notes)
    assert any("上游材料不可用" in p for p in problems), problems

    # Prose mentioning the words must not trip it. Sized past the budget gate
    # so this isolates the substring behaviour and nothing else.
    problems.clear()
    noisy = ("以下是本轮上游阶段已生成的全部文档。"
             "某雷达指出：若某资源不可用则需人工复核。\n\n"
             + "内容" * 20000)
    monkeypatch.setattr(checker, "upstream_for",
                        lambda key, job, date, root="": noisy)
    checker.check_synthesis_got_its_input("2026-01-02", problems, notes)
    assert problems == [], problems


def test_an_unsubstituted_placeholder_is_caught(checker, tmp_path):
    """A prompt the engine never filled reaches the model as literal text."""
    out = tmp_path / "output"
    _seed(out, "2026-01-02",
          p9_body="# 报告\n\n{upstream_reports}\n\n内容。\n")
    problems: list[str] = []
    checker.check_no_placeholder_survived(
        out / "practical_ai_intelligence" / "2026-01-02", problems)
    assert any("{upstream_reports}" in p for p in problems), problems


def test_a_stale_citation_figure_is_caught(checker, tmp_path):
    """Metadata is written once, at run time, and never revisited.

    So after the "（" extractor bug was fixed, every past round kept reporting
    the number the broken extractor produced. Nothing said so.
    """
    out = tmp_path / "output"
    date = "2026-01-02"
    body = ("# 报告\n\n"
            + "见 https://a.test/1（2026-09-22 发布）。\n" * 3
            + "另有 https://b.test/2 与 https://c.test/3。\n")
    _seed(out, date, p9_body=body)
    meta = tmp_path / "report_meta.jsonl"
    meta.write_text(json.dumps({
        "ts": f"{date}T06:00:00+0800", "job": "practical_ai_intelligence/06_infra_and_eval_radar",
        "citation_check": {"total": 12, "matched": 6, "coverage": 0.5},
    }, ensure_ascii=False) + "\n", encoding="utf-8")

    problems: list[str] = []
    notes: list[str] = []
    checker.check_citations(out / "practical_ai_intelligence" / date, meta, date,
                            0.6, problems, notes)
    assert any("已过时" in p for p in problems), problems


def test_markdown_in_a_stored_artifact_is_caught(checker, tmp_path):
    """The stored JSON, not a fresh parse.

    A fresh parse is always clean -- the parser strips markup on the way in --
    so this can only be caught in the artifact a consumer actually reads, and
    only if that artifact was written before the fix. Which is exactly the
    2026-09-26 situation: 23 of its priorities were stored as "**P0**".
    """
    out = tmp_path / "output"
    _seed(out, "2026-01-02", p9_body=_body())
    (out / "practical_ai_intelligence" / "2026-01-02" / "action_items.json").write_text(
        json.dumps({"actions": [{"action": "**A1. 冻结前缀**", "priority": "**P0**"},
                                {"action": "A2. 改成本", "priority": "P1"}],
                    "tests": [{"test": "T1", "priority": "P0"}]}, ensure_ascii=False),
        encoding="utf-8")

    problems: list[str] = []
    notes: list[str] = []
    checker.check_actions(out / "practical_ai_intelligence" / "2026-01-02",
                          problems, notes)
    assert any("markdown 标记" in p for p in problems), problems
    assert any("不是 P0/P1/P2" in p for p in problems), problems


def test_the_script_runs_and_its_exit_code_means_something(tmp_path):
    """It is meant to gate a run, so the exit code is the contract."""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path / "nothing")],
        capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 1
    assert "找不到任何轮次" in proc.stderr
