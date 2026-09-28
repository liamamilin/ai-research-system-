"""Does retrieval find the passage that answers a real question?

Retrieval quality cannot be improved by intuition, and asking the model
whether it got what it needed costs an LLM call per sample and answers a
different question. This is the cheap objective version: a question, a phrase
that only appears in the passage that should answer it, and the round that
passage belongs to.

Both parts matter, because there are two distinct failures. The answering
passage may be absent from the results at all; or it may be present while the
results are padded with older rounds that discuss the same subject. Only the
second check catches the second failure.

Run it directly to see the current state:

    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (question, filename that should answer it, phrase unique to that passage,
#  round it should come from -- "" means any)
#
# The gold phrase must be a literal substring of a chunk of the *newest* round's
# copy of ``filename``, and it must be a section heading, so it lands at a chunk
# boundary where the retriever can see it. ``check_markers`` proves both
# properties against the index before any scoring happens: a phrase the reports
# do not contain measures nothing, and a marker that is absent from every chunk
# is a broken test, not a retrieval failure. The first draft of this file scored
# 0/14 that way -- it used the Chinese headings from the P9 *prompt* against
# reports whose headings are in English.
CASES: list[tuple[str, str, str]] = [
    ("P7 判定为最高优先级的内容机会有哪几项？",
     "07_product_content_opportunities.md", "最佳内容机会"),
    ("P7 判定不值得做哪些内容，理由是什么？",
     "07_product_content_opportunities.md", "忽略 / 不值得做内容"),
    ("P7 给出的工具推荐机会是什么？",
     "07_product_content_opportunities.md", "工具推荐机会"),
    ("P9 的立即行动有哪些？",
     "09_executive_synthesis_and_actions.md", "Immediate Actions"),
    ("P9 要求本周做哪些测试？",
     "09_executive_synthesis_and_actions.md", "Test This Week"),
    ("P9 的模型路由建议是什么？",
     "09_executive_synthesis_and_actions.md", "Model Routing Implications"),
    ("P9 对工具订阅采购的判断是什么？",
     "09_executive_synthesis_and_actions.md", "Tool Subscription"),
    ("P9 忽略了哪些主题？",
     "09_executive_synthesis_and_actions.md", "Ignored Themes"),
    ("P9 的风险响应计划是什么？",
     "09_executive_synthesis_and_actions.md", "Risk Response Plan"),
    ("本轮最严重的风险是什么？",
     "08_risk_and_alternatives.md", "关键风险"),
    ("供应商依赖集中在哪里？",
     "08_risk_and_alternatives.md", "供应商依赖地图"),
    ("备份方案是什么？",
     "08_risk_and_alternatives.md", "备份方案"),
    ("编码工具套餐之间怎么对比？",
     "01_model_and_pricing_radar.md", "AI Coding Plan Comparison"),
    ("哪些模型和套餐值得实测？",
     "01_model_and_pricing_radar.md", "Test Candidates"),
    ("模型 API 价格有什么变化？",
     "01_model_and_pricing_radar.md", "Pricing Comparison Notes"),
]


def check_markers(date: str) -> list[str]:
    """Gold phrases the corpus does not contain, and so cannot be retrieved.

    A marker is checked against the full chunk text, because that is what a
    retrieval hit now carries. Anything the reports do not literally contain
    measures nothing, and reporting it as a retrieval miss would blame the
    retriever for a broken test. The first draft of this file scored 0/14 that
    way, twice over: it used the Chinese headings from the P9 *prompt* against
    reports whose headings are in English, and it demanded headings fit inside
    the old 300-char window when several sit past offset 1000.
    """
    from web.indexer import db as index_db

    with index_db.connect() as conn:
        rows = conn.execute(
            "SELECT path, content FROM report_chunks WHERE path LIKE ?",
            (f"%/{date}/%",),
        ).fetchall()

    broken = []
    for _question, filename, gold in CASES:
        found = any(
            r["path"].endswith(filename) and gold in (r["content"] or "")
            for r in rows
        )
        if not found:
            broken.append(f"{filename} 中找不到「{gold}」")
    return broken


def newest_round(output_dir: Path) -> str:
    pipeline = Path(output_dir) / "practical_ai_intelligence"
    rounds = sorted(
        p.name for p in pipeline.iterdir()
        if p.is_dir() and (p / "09_executive_synthesis_and_actions.md").is_file()
    )
    if not rounds:
        raise SystemExit("找不到任何完整轮次")
    return rounds[-1]


def evaluate(date: str | None = None, limit: int = 6, verbose: bool = False) -> dict:
    from web.indexer import vectors
    from web.routes.qa import _retrieval_settings
    from core import load_system_config
    from web.settings import get_settings

    settings = get_settings()
    sys_config = load_system_config(settings.paths.config_dir)
    retrieval = _retrieval_settings(sys_config)
    date = date or newest_round(settings.paths.output_dir)

    from web.routes.qa import _embed_query

    broken = check_markers(date)
    if broken:
        raise SystemExit(
            "以下 gold 标记在 " + date + " 轮次的索引里找不到，"
            "评估结果不可信：\n  - " + "\n  - ".join(broken))

    found_pass = 0
    found_round = 0
    rows = []
    for question, filename, gold_text in CASES:
        vector = _embed_query(question, sys_config)
        hits = vectors.hybrid_search(question, vector, limit=limit, **retrieval)
        match = next(
            (i for i, h in enumerate(hits)
             if gold_text in (h.get("snippet") or "")
             and h["path"].endswith(filename)),
            None,
        )
        ok = match is not None
        found_pass += 1 if ok else 0
        # The answering passage exists in many rounds. Retrieving a June copy
        # of a question about *this* week's risk is a miss, not a hit.
        in_round = ok and date in hits[match]["path"]
        found_round += 1 if in_round else 0
        rows.append({
            "question": question, "ok": ok, "rank": match, "in_round": in_round,
            "gold": gold_text, "file": filename,
            "got": [h["path"].split("/", 1)[-1][:34] for h in hits] if verbose else [],
        })

    total = len(CASES)
    return {"date": date, "total": total, "pass": found_pass,
            "in_round": found_round, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="评估报告检索能否命中答案所在段落")
    parser.add_argument("--date", help="轮次日期（默认最新）")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--verbose", action="store_true", help="打印召回的文档")
    args = parser.parse_args()

    result = evaluate(args.date, limit=args.limit, verbose=args.verbose)
    print(f"检索评估 · 轮次 {result['date']} · top-{args.limit}\n")
    for row in result["rows"]:
        mark = "✓" if row["in_round"] else ("·" if row["ok"] else "×")
        if row["ok"] and row["in_round"]:
            note = f"第 {row['rank'] + 1} 位"
        elif row["ok"]:
            note = f"第 {row['rank'] + 1} 位 · 命中的是更早的轮次"
        else:
            note = "未命中"
        print(f"  {mark} {note:<26} {row['question']}")
        if not row["ok"]:
            print(f"      期望在 {row['file']} 中找到「{row['gold']}」")
        if args.verbose:
            print(f"      召回: {', '.join(row['got'])}")
    print()
    print(f"命中答案段落      {result['pass']}/{result['total']}"
          f"  ({result['pass'] * 100 // result['total']}%)")
    print(f"且来自 {result['date']} 轮次"
          f"  {result['in_round']}/{result['total']}"
          f"  ({result['in_round'] * 100 // result['total']}%)")
    return 0 if result["in_round"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
