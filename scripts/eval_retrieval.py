"""Does retrieval find the passage that answers a real question?

Retrieval quality cannot be improved by intuition, and asking the model whether
it got what it needed costs an LLM call per sample and answers a different
question. This is the cheap objective version: a question, the section that
should answer it, and the round that section belongs to.

Three things are measured, and they come apart in ways that matter:

* the answering **section** was retrieved;
* it came from the **current** round -- a June copy of a question about this
  week's risk is a miss, not a hit;
* the right **report** arrived at all, which is the loosest of the three and
  separates "wrong section" from "never found the document".

Run it directly to see the current state:

    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py --date 2026-09-28
    python scripts/eval_retrieval.py --verbose

Two rules this file follows after being wrong three times:

* **Score the section, not the chunk.** A section spans several chunks and the
  answer is usually in one carrying no heading at all.
* **A gold phrase the reports do not contain measures nothing.** The marker
  check refuses to report rather than blaming the retriever, and each marker
  lists the phrasings actually in use -- the model drifts between rounds, from
  "最佳内容机会" to "顶级内容机会", "本周测试" to "本周应做的测试", and an
  instrument that assumes one phrasing goes stale silently.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# (question, file that should answer it, headings seen for that section)
CASES: list[tuple[str, str, list[str]]] = [
    # P1 模型与定价
    ("哪些模型值得实测？",
     "01_model_and_pricing_radar.md", ["Test Candidates", "建议测试的候选"]),
    ("编码工具的订阅套餐该怎么挑？",
     "01_model_and_pricing_radar.md", ["AI Coding Plan Comparison", "AI Coding Plan 对比表"]),
    ("模型 API 价格最近有什么变化？",
     "01_model_and_pricing_radar.md", ["Pricing Comparison Notes", "定价对照说明"]),
    ("这轮有哪些值得注意的模型发布？",
     "01_model_and_pricing_radar.md", ["High-Signal Model Updates", "高信号模型更新"]),
    ("P1 给的模型路由建议是什么？",
     "01_model_and_pricing_radar.md", ["Model Routing Implications", "对模型路由的影响"]),
    # P2 AI 编码工具
    ("有哪些 AI 编码工具值得试？",
     "02_ai_coding_tools_radar.md", ["Tools Worth Testing", "值得测试的工具"]),
    ("这几个编码工具的对比表长什么样？",
     "02_ai_coding_tools_radar.md", ["Tool Comparison Table", "工具对比表"]),
    ("这些工具对我们写代码的流程有什么影响？",
     "02_ai_coding_tools_radar.md", ["Coding Workflow Implications", "编码工作流影响"]),
    # P3 Agent 工作流
    ("Agent 架构上有什么新模式？",
     "03_agent_workflow_radar.md", ["架构模式"]),
    ("哪些该换成新方案、哪些该留着？",
     "03_agent_workflow_radar.md", ["Replace / Keep / Watch", "替换 / 保留 / 关注"]),
    ("建议我去做哪些实验？",
     "03_agent_workflow_radar.md", ["建议实验"]),
    # P4 项目理解
    ("项目理解这块有什么高价值发现？",
     "04_project_understanding_radar.md", ["高价值发现"]),
    ("有什么基准测试的思路可以借鉴？",
     "04_project_understanding_radar.md", ["基准思路", "基准构想"]),
    # P5 上下文 / RAG / 记忆
    ("上下文和 RAG 有什么架构模式？",
     "05_context_rag_memory_radar.md", ["上下文架构模式"]),
    ("这些发现能怎么用到我们项目上？",
     "05_context_rag_memory_radar.md", ["对当前项目的应用"]),
    # P6 基建与评测
    ("评测和基建有什么更新？",
     "06_infra_and_eval_radar.md", ["Infrastructure Updates", "基础设施更新"]),
    ("有哪些评测或基准的进展？",
     "06_infra_and_eval_radar.md", ["Evaluation / Benchmark Updates", "评测 / 基准更新"]),
    ("基建这边有什么工程建议？",
     "06_infra_and_eval_radar.md", ["Engineering Recommendations", "工程建议"]),
    # P7 内容与产品机会
    ("P7 判定为最高优先级的内容机会有哪几项？",
     "07_product_content_opportunities.md", ["最佳内容机会", "顶级内容机会"]),
    ("可以做哪些工具推荐类的内容？",
     "07_product_content_opportunities.md", ["工具推荐机会"]),
    ("有哪些基准类内容机会？",
     "07_product_content_opportunities.md", ["基准内容机会"]),
    ("P7 判定哪些内容不值得做？",
     "07_product_content_opportunities.md", ["忽略 / 不值得做内容", "不值得做内容"]),
    # P8 风险
    ("本轮最严重的风险是什么？",
     "08_risk_and_alternatives.md", ["关键风险"]),
    ("供应商依赖集中在哪里？",
     "08_risk_and_alternatives.md", ["供应商依赖地图"]),
    ("成本上的风险大吗？",
     "08_risk_and_alternatives.md", ["成本风险"]),
    ("备份方案是什么？",
     "08_risk_and_alternatives.md", ["备份方案", "备用方案"]),
    # P9 执行摘要与行动
    ("P9 的立即行动有哪些？",
     "09_executive_synthesis_and_actions.md", ["Immediate Actions", "立即行动"]),
    ("本周要测什么？",
     "09_executive_synthesis_and_actions.md", ["Test This Week", "本周测试"]),
    ("P9 对工具订阅采购的判断是什么？",
     "09_executive_synthesis_and_actions.md", ["Tool Subscription", "工具订阅"]),
    ("P9 在 Agent 架构上给出了什么结论？",
     "09_executive_synthesis_and_actions.md", ["Agent Architecture Implications", "Agent 架构"]),
    ("P9 忽略了哪些主题？",
     "09_executive_synthesis_and_actions.md", ["Ignored Themes", "已忽略主题"]),
]

_HEADING = re.compile(r"^#{1,6}\s")


def _covers(section: str, golds: list[str]) -> bool:
    return any(gold in section for gold in golds)


def newest_round(output_dir) -> str:
    pipeline = Path(output_dir) / "practical_ai_intelligence"
    rounds = sorted(
        p.name for p in pipeline.iterdir()
        if p.is_dir() and (p / "09_executive_synthesis_and_actions.md").is_file()
    )
    if not rounds:
        raise SystemExit("找不到任何完整轮次")
    return rounds[-1]


def section_index() -> dict[tuple, list[str]]:
    """Map every stored chunk to the sections it covers.

    Scoring a hit by whether its own text contains the section heading is
    wrong, and it was wrong here for a while: a section is split across several
    chunks, so the passage that answers the question is usually a body chunk
    with no heading on it. Ten of fifteen "misses" were the right section
    arriving without its title.

    The first fix only looked for a heading at the *start* of a chunk and
    scored worse, because these reports put a heading at the end of a long
    table: no chunk begins with `## `, so every chunk in the executive synthesis
    was attributed to the document title.

    So boundaries are tracked through the chunk sequence, and a chunk that
    straddles a heading is credited to both sections -- which is the truth,
    since the text either side of the heading is in the same excerpt.
    """
    from web.indexer import db as index_db

    with index_db.connect() as conn:
        rows = conn.execute(
            "SELECT path, chunk_index, content FROM report_chunks "
            "ORDER BY path, chunk_index").fetchall()

    index: dict[tuple, list[str]] = {}
    current_path = None
    sections: list[str] = []
    for row in rows:
        if row["path"] != current_path:
            current_path, sections = row["path"], []
        for line in (row["content"] or "").split("\n"):
            if _HEADING.match(line):
                name = line.strip().lstrip("#").strip()
                if name and (not sections or sections[-1] != name):
                    sections.append(name)
        index[(row["path"], row["chunk_index"])] = list(sections)
    return index


def _hit_covers(hit: dict, index: dict[tuple, list[str]], golds: list[str]) -> bool:
    seen = index.get((hit.get("path"), hit.get("chunk_index"))) or []
    return _covers(" ".join(seen), golds)


def check_markers(date: str) -> list[str]:
    """Cases whose section the corpus does not contain at all.

    A marker that matches nothing measures nothing, and reporting it as a
    retrieval miss blames the retriever for a broken test. Each case lists
    every phrasing seen so far, and this refuses to score if none of them is
    present -- the right behaviour is to say so, not to publish a number.
    """
    from web.indexer import db as index_db

    with index_db.connect() as conn:
        rows = conn.execute(
            "SELECT path, content FROM report_chunks WHERE path LIKE ?",
            (f"%/{date}/%",),
        ).fetchall()

    broken = []
    for _question, filename, golds in CASES:
        found = any(
            r["path"].endswith(filename) and _covers(r["content"] or "", golds)
            for r in rows
        )
        if not found:
            broken.append(f"{filename} 中找不到 {' / '.join(golds)}")
    return broken


def evaluate(date: str | None = None, limit: int = 6, verbose: bool = False) -> dict:
    from web.indexer import vectors
    from web.routes.qa import _retrieval_settings, _embed_query
    from core import load_system_config
    from web.settings import get_settings

    settings = get_settings()
    sys_config = load_system_config(settings.paths.config_dir)
    retrieval = _retrieval_settings(sys_config)
    date = date or newest_round(settings.paths.output_dir)

    broken = check_markers(date)
    if broken:
        raise SystemExit(
            f"以下 gold 标记在 {date} 轮次的索引里一个措辞都没有，"
            "评估结果不可信：\n  - " + "\n  - ".join(broken))

    sections = section_index()
    pass_ = current = report_ok = 0
    rows = []
    for question, filename, golds in CASES:
        vector = _embed_query(question, sys_config)
        hits = vectors.hybrid_search(question, vector, limit=limit, **retrieval)
        match = next(
            (i for i, h in enumerate(hits)
             if h["path"].endswith(filename) and _hit_covers(h, sections, golds)),
            None,
        )
        ok = match is not None
        pass_ += 1 if ok else 0
        in_round = ok and date in hits[match]["path"]
        current += 1 if in_round else 0
        reached = any(h["path"].endswith(filename) and date in h["path"] for h in hits)
        report_ok += 1 if reached else 0
        rows.append({
            "question": question, "ok": ok, "rank": match, "in_round": in_round,
            "report_ok": reached, "gold": " / ".join(golds), "file": filename,
            "got": [h["path"].split("/", 1)[-1][:34] for h in hits] if verbose else [],
        })

    total = len(CASES)
    return {"date": date, "total": total, "pass": pass_, "current": current,
            "report": report_ok, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="评估报告检索能否命中答案所在小节")
    parser.add_argument("--date", help="轮次日期（默认最新）")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--verbose", action="store_true", help="打印召回的文档")
    args = parser.parse_args()

    result = evaluate(args.date, limit=args.limit, verbose=args.verbose)
    print(f"检索评估 · 轮次 {result['date']} · top-{args.limit}\n")
    for row in result["rows"]:
        if row["in_round"]:
            mark, note = "✓", f"第 {row['rank'] + 1} 位"
        elif row["ok"]:
            mark, note = "·", f"第 {row['rank'] + 1} 位 · 命中的是更早的轮次"
        elif row["report_ok"]:
            mark, note = "◦", "报告对，小节没命中"
        else:
            mark, note = "×", "报告也没召回"
        print(f"  {mark} {note:<24} {row['question']}")
        if not row["ok"]:
            print(f"      期望在 {row['file']} 中找到「{row['gold']}」小节")
        if args.verbose:
            print(f"      召回: {', '.join(row['got'])}")
    pct = lambda n: f"{n * 100 // result['total']}%"
    print()
    print(f"命中答案小节      {result['pass']}/{result['total']}  ({pct(result['pass'])})")
    print(f"且来自 {result['date']} 轮次"
          f"  {result['current']}/{result['total']}  ({pct(result['current'])})")
    print(f"（参考）召回到正确报告"
          f"  {result['report']}/{result['total']}  ({pct(result['report'])})")
    return 0 if result["report"] == result["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
