#!/usr/bin/env python
"""Check a finished round for the ways it can be quietly wrong.

Every bug found in the 2026-09-28 review had the same shape: the round reported
success, and something that matters was missing or wrong. A round finished
10/10 with an empty watchlist. A round where the synthesis stages ran with no
input at all. A round whose citation figure was 15 points low because of a
character in a regex. A round whose priority badges were all the same colour.

The unit suite catches those now, but only for the shapes someone thought to
write down. This reads an actual finished round -- the documents, the exported
artifacts, the recorded metadata -- and asks whether the numbers hold up. Run
it after a round, or on any past round:

    python scripts/check_round_quality.py                  # newest round
    python scripts/check_round_quality.py 2026-09-28       # a specific one
    python scripts/check_round_quality.py --min-coverage 0.8

Exit code 1 when something is wrong, so it can gate a run.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline_docs import (  # noqa: E402
    STAGE_DEPS,
    STAGE_FILE,
    STAGE_LABEL,
    STAGES,
    UPSTREAM_CHAR_BUDGET,
    upstream_for,
)
from core.artifacts import parse_action_items, parse_watchlist  # noqa: E402
from core.provenance import extract_urls  # noqa: E402

PIPELINE_DIR = "practical_ai_intelligence"
_MARKDOWN_IN_FIELD = re.compile(r"\*\*|`")
# The first words of the "no upstream" message from core.pipeline_docs, used to
# recognise it without matching prose inside the documents themselves.
_UPSTREAM_UNAVAILABLE = "（本轮上游文档不可用"


def newest_round(output_dir: Path) -> str | None:
    pipeline = output_dir / PIPELINE_DIR
    if not pipeline.is_dir():
        return None
    rounds = sorted(
        p.name for p in pipeline.iterdir()
        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
        and any(p.glob("*.md"))
    )
    return rounds[-1] if rounds else None


def stage_documents(round_dir: Path) -> dict[str, Path]:
    return {key: round_dir / fname for key, fname, _ in STAGES
            if (round_dir / fname).is_file()}


def check_stages_exist(round_dir: Path, problems: list[str], notes: list[str]) -> None:
    docs = stage_documents(round_dir)
    missing = [STAGE_LABEL.get(k, k) for k, _f, _l in STAGES if k not in docs]
    if missing:
        problems.append(f"缺少 {len(missing)} 个阶段文档：{'、'.join(missing)}")
    else:
        notes.append(f"10 个阶段文档齐全（{len(docs)}）")


def check_synthesis_got_its_input(date: str, problems: list[str], notes: list[str],
                                 jobs_dir: Path | None = None) -> None:
    """The daily path can run the synthesis stages with nothing attached.

    This happened for as long as the upstream documents were only wired into the
    Web runner: the stages received a literal ``{upstream_reports}`` placeholder
    and a paragraph claiming the radars were attached. Nothing failed.
    """
    from core import load_job

    jobs_root = jobs_dir or (ROOT / "jobs")
    for key in ("07_product_content_opportunities", "08_risk_and_alternatives",
                "09_executive_synthesis_and_actions"):
        job = load_job(str(jobs_root), f"{PIPELINE_DIR}/{key}")
        if not job:
            problems.append(f"{STAGE_LABEL.get(key, key)}：找不到 job 定义")
            continue
        upstream = upstream_for(key, job, date, root=str(ROOT))
        if not upstream:
            continue
        # Match the sentinel, not a substring: the documents themselves talk
        # about unavailable inputs, and a plain "in" test fired on their prose.
        if upstream.startswith(_UPSTREAM_UNAVAILABLE):
            detail = upstream[len(_UPSTREAM_UNAVAILABLE):].lstrip("：: ")
            problems.append(
                f"{STAGE_LABEL.get(key, key)}：上游材料不可用 —— {detail[:120]}")
        elif len(upstream) < UPSTREAM_CHAR_BUDGET * 0.5:
            problems.append(
                f"{STAGE_LABEL.get(key, key)}：只拿到 {len(upstream)} 字符上游材料"
                f"（预算 {UPSTREAM_CHAR_BUDGET}）")
        else:
            notes.append(
                f"{STAGE_LABEL.get(key, key)}：上游 {len(upstream)} 字符")


def check_no_placeholder_survived(round_dir: Path, problems: list[str]) -> None:
    """A prompt the engine never substituted reaches the model as literal text."""
    for key, path in stage_documents(round_dir).items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for placeholder in re.findall(r"\{[a-z_][a-z0-9_]*\}", text):
            if placeholder in ("{date}", "{date_7d_ago}"):
                continue
            problems.append(
                f"{STAGE_LABEL.get(key, key)}：正文里残留未替换的占位符 {placeholder}")


def check_watchlist(round_dir: Path, problems: list[str], notes: list[str]) -> None:
    p9 = round_dir / STAGE_FILE["09_executive_synthesis_and_actions"]
    if not p9.is_file():
        return
    size = p9.stat().st_size
    if size < 8000:
        problems.append(f"P9 只有 {size} 字节，综合阶段可能没拿到输入")

    text = p9.read_text(encoding="utf-8", errors="replace")
    parsed = parse_watchlist(text)
    items = parsed["items"]
    if not items:
        problems.append("观察清单解析出 0 项")
        return
    for warning in parsed["warnings"]:
        if "未识别" in warning:
            problems.append(f"观察清单有未识别的表头：{warning}")
    # A watch item must be traceable to something, so a missing source is a
    # problem. A trigger signal is requested by the current P9 output spec but
    # older rounds predate it, so its absence is reported, not failed on.
    unsourced = [i for i in items if not i.get("evidence")]
    if unsourced:
        problems.append(
            f"观察清单 {len(unsourced)}/{len(items)} 条没有来源 URL")
    else:
        notes.append(f"观察清单 {len(items)} 条，均带来源 URL")
    untriggered = [i for i in items if not i.get("trigger")]
    if untriggered:
        notes.append(
            f"其中 {len(untriggered)} 条没有触发信号（可能用了列表而非表格）")

    path = round_dir / "watchlist.json"
    if path.is_file():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            problems.append(f"watchlist.json 不是合法 JSON：{exc}")
            return
        if not (stored.get("items") or []):
            # The document is fine but the export is not: the board shows the
            # export, so this is what a reader actually sees.
            problems.append("watchlist.json 为空，但 P9 文档里有可解析的观察项")


def check_actions(round_dir: Path, problems: list[str], notes: list[str]) -> None:
    """Inspect the *stored* artifact, not a fresh parse.

    A fresh parse is always clean -- the parser strips presentation markup on
    the way in, so checking it would be tautological and could never fail. The
    exported JSON is what a consumer actually reads, and it can legitimately
    hold dirty values because it was written by an older parser.
    """
    stored = round_dir / "action_items.json"
    if not stored.is_file():
        notes.append("没有 action_items.json，跳过产物字段检查")
        return
    try:
        payload = json.loads(stored.read_text(encoding="utf-8"))
    except ValueError as exc:
        problems.append(f"action_items.json 不是合法 JSON：{exc}")
        return

    for name, rows in (("行动", payload.get("actions") or []),
                       ("测试", payload.get("tests") or [])):
        rows = [r for r in rows if isinstance(r, dict)]
        if not rows:
            problems.append(f"action_items.json 里{name}为 0 项")
            continue
        dirty = [
            r for r in rows
            if any(_MARKDOWN_IN_FIELD.search(v) for v in r.values()
                   if isinstance(v, str))
        ]
        if dirty:
            problems.append(
                f"{name}有 {len(dirty)}/{len(rows)} 项字段里带 markdown 标记"
                f"（该产物由旧版解析器生成）")
        bad_priority = [
            r.get("priority") for r in rows
            if r.get("priority") and not re.fullmatch(r"P\d+", r["priority"].strip())
        ]
        if bad_priority:
            problems.append(
                f"{name}有 {len(bad_priority)} 条优先级不是 P0/P1/P2 形式："
                f"{sorted(set(bad_priority))[:4]}")
        notes.append(f"{name} {len(rows)} 项")


def check_citations(round_dir: Path, meta_path: Path, date: str,
                    min_coverage: float, problems: list[str],
                    notes: list[str]) -> None:
    """Coverage is computed by matching strings, so it can drift silently."""
    if not meta_path.is_file():
        notes.append("没有 report_meta.jsonl，跳过引用覆盖率检查")
        return
    records = []
    for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if str(record.get("ts", "")).startswith(date):
            records.append(record)
    if not records:
        notes.append("report_meta 里没有本轮记录，跳过引用覆盖率检查")
        return

    worst = None
    for record in records:
        check = record.get("citation_check") or {}
        coverage = check.get("coverage")
        if coverage is None:
            continue
        job = str(record.get("job", "")).split("/")[-1]
        # A recorded citation count that no longer matches what the current
        # extractor finds in the document means the figure was measured with a
        # different (broken) parser and is stale. This is how the 2026-09-28
        # round kept reporting P6 at 56% long after the "（" bug was fixed --
        # the metadata is written once, at run time, and never revisited.
        doc = round_dir / STAGE_FILE.get(job, "")
        if doc.is_file():
            now = len(extract_urls(
                doc.read_text(encoding="utf-8", errors="replace")))
            recorded = int(check.get("total") or 0)
            if recorded and abs(now - recorded) > max(2, recorded * 0.05):
                problems.append(
                    f"{job}：记录的 {recorded} 条引用与当前解析出的 {now} 条不符"
                    f"—— 该轮的覆盖率是用旧解析器算的，已过时")
                coverage = None
        if coverage is None:
            continue
        if coverage < min_coverage:
            problems.append(
                f"{job}：引用覆盖率 {coverage:.0%}，低于阈值 {min_coverage:.0%}")
        if worst is None or coverage < worst[1]:
            worst = (job, coverage, check.get("total", 0))
    if worst:
        notes.append(
            f"引用覆盖率最低的是 {worst[0]}：{worst[1]:.0%}（{worst[2]} 条引用）")

    # Direct check on the document, independent of the recorded number.
    for key, path in stage_documents(round_dir).items():
        text = path.read_text(encoding="utf-8", errors="replace")
        urls = extract_urls(text)
        if not urls:
            continue
        if len(urls) < 8:
            problems.append(
                f"{STAGE_LABEL.get(key, key)}：只解析出 {len(urls)} 条 URL，正文可能被截断")


def check_ratios(round_dir: Path, problems: list[str], notes: list[str]) -> None:
    """Truncation is a budget decision, not an accident -- but it should be seen."""
    for key, path in stage_documents(round_dir).items():
        deps = STAGE_DEPS.get(key) or ()
        if not deps:
            continue
        share = max(2000, UPSTREAM_CHAR_BUDGET // len(deps))
        used = 0
        for dep in deps:
            dep_path = round_dir / STAGE_FILE[dep]
            if dep_path.is_file():
                used += min(dep_path.stat().st_size, share)
        if used < share * len(deps) * 0.6:
            problems.append(
                f"{STAGE_LABEL.get(key, key)}：上游可用材料只有 {used} 字符，"
                f"明显不足（应约 {share * len(deps)}）")


def main() -> int:
    parser = argparse.ArgumentParser(description="检查一轮情报产物的完整性")
    parser.add_argument("date", nargs="?", help="轮次日期 YYYY-MM-DD（默认最新）")
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    parser.add_argument("--min-coverage", type=float, default=0.6)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    date = args.date or newest_round(output_dir)
    if not date:
        print("找不到任何轮次目录", file=sys.stderr)
        return 1
    round_dir = output_dir / PIPELINE_DIR / date
    if not round_dir.is_dir():
        print(f"轮次目录不存在：{round_dir}", file=sys.stderr)
        return 1

    problems: list[str] = []
    notes: list[str] = []

    print(f"检查轮次 {date}\n")
    check_stages_exist(round_dir, problems, notes)
    check_synthesis_got_its_input(date, problems, notes)
    check_no_placeholder_survived(round_dir, problems)
    check_watchlist(round_dir, problems, notes)
    check_actions(round_dir, problems, notes)
    check_citations(round_dir, ROOT / "state" / "report_meta.jsonl", date,
                    args.min_coverage, problems, notes)
    check_ratios(round_dir, problems, notes)

    for note in notes:
        print(f"  · {note}")
    if problems:
        print()
        for problem in problems:
            print(f"  ✗ {problem}")
        print(f"\n{len(problems)} 个问题")
        return 1
    print("\n没有发现问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
