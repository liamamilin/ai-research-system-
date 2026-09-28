"""The intelligence round's documents: what they are and how they are handed on.

This lives in ``core`` rather than ``web/runner/pipeline.py`` because two
different runners execute this pipeline and both need the same knowledge. The
Web console drives stages in-process; the launchd agent that runs the daily
round drives each one as a separate ``run.py`` process. When the dependency
graph and the collector lived only in the Web runner, the daily round silently
skipped them: P7, P8 and P9 were sent a literal ``{upstream_reports}``
placeholder and a paragraph promising documents that were never attached, so
the synthesis stages ran with no input at all and nothing anywhere said so.

Both runners now import from here, so the two cannot drift apart again.
"""

from __future__ import annotations

import os
import re

PIPELINE_DIR = "practical_ai_intelligence"

# (job basename, output filename, UI label)
STAGES: list[tuple[str, str, str]] = [
    ("00_collection_planner", "00_collection_plan.md", "P0 采集计划"),
    ("01_model_and_pricing_radar", "01_model_and_pricing_radar.md", "P1 模型定价"),
    ("02_ai_coding_tools_radar", "02_ai_coding_tools_radar.md", "P2 编程工具"),
    ("03_agent_workflow_radar", "03_agent_workflow_radar.md", "P3 Agent 工作流"),
    ("04_project_understanding_radar", "04_project_understanding_radar.md", "P4 项目理解"),
    ("05_context_rag_memory_radar", "05_context_rag_memory_radar.md", "P5 上下文/RAG"),
    ("06_infra_and_eval_radar", "06_infra_and_eval_radar.md", "P6 基础设施/评测"),
    ("07_product_content_opportunities", "07_product_content_opportunities.md", "P7 产品内容机会"),
    ("08_risk_and_alternatives", "08_risk_and_alternatives.md", "P8 风险与替代"),
    ("09_executive_synthesis_and_actions", "09_executive_synthesis_and_actions.md", "P9 执行综合"),
]

GROUPS: list[tuple[str, list[str]]] = [
    ("P0", ["00_collection_planner"]),
    ("P1-P6", [
        "01_model_and_pricing_radar",
        "02_ai_coding_tools_radar",
        "03_agent_workflow_radar",
        "04_project_understanding_radar",
        "05_context_rag_memory_radar",
        "06_infra_and_eval_radar",
    ]),
    ("P7-P8", [
        "07_product_content_opportunities",
        "08_risk_and_alternatives",
    ]),
    ("P9", ["09_executive_synthesis_and_actions"]),
]

# Real data dependencies. P1-P6 are independent radars; P7/P8 synthesise them
# and P9 synthesises P7/P8. Re-running a stage therefore invalidates whatever
# consumed its output.
_RADAR_KEYS = tuple(key for key, _, _ in STAGES[1:7])
STAGE_DEPS: dict[str, tuple[str, ...]] = {
    STAGES[0][0]: (),
    **{key: () for key in _RADAR_KEYS},
    "07_product_content_opportunities": _RADAR_KEYS,
    "08_risk_and_alternatives": _RADAR_KEYS,
    "09_executive_synthesis_and_actions": (
        "07_product_content_opportunities", "08_risk_and_alternatives",
    ),
}

STAGE_FILE = {key: fname for key, fname, _ in STAGES}
STAGE_LABEL = {key: label for key, _, label in STAGES}

# How much upstream text to hand a synthesis stage. The radars run to ~25k
# characters each, so all six is ~150k -- the entire research context budget,
# spent before the model does any work. Truncation is stated in the prompt
# rather than silent.
UPSTREAM_CHAR_BUDGET = 60000
MIN_UPSTREAM_CHARS = 2000

# Markdown heading that starts a top-level section. Radars are built from these,
# which is what makes it possible to keep sections whole.
_SECTION_RE = re.compile(r"^##\s+\S", re.MULTILINE)


def _split_sections(text: str) -> tuple[str, list[str]]:
    """Split into (preamble, [section, ...]) on top-level ``##`` headings."""
    starts = [m.start() for m in _SECTION_RE.finditer(text)]
    if not starts:
        return text, []
    preamble = text[:starts[0]]
    bounds = starts + [len(text)]
    return preamble, [text[bounds[i]:bounds[i + 1]] for i in range(len(starts))]


def _clip(text: str, limit: int) -> str:
    """Keep both ends of a single oversized section."""
    if len(text) <= limit:
        return text
    marker = "\n\n…（本节中间省略）…\n\n"
    room = max(2, limit - len(marker))
    head = max(1, int(room * 0.65))
    tail = room - head
    if tail <= 0:
        return text[:limit]
    return text[:head] + marker + text[-tail:]


def _select_sections(text: str, limit: int, path: str = "") -> str:
    """Fit ``text`` into ``limit`` characters, keeping whole sections from both ends.

    Truncating at the head is the wrong end. A radar opens with framing and
    high-signal items and closes with the comparison table, the per-tool
    breakdown and the low-signal list -- and the synthesis stages are told
    precisely to use those: P7's prompt asks for "Plan comparisons", "Tool
    recommendations" and "Ignore / Not Worth Content". At the 10k-per-dependency
    share, P1 kept sections 1.1-1.9 and lost 2-7, so P7 was handed the framing
    and none of the material it was told to build on.

    The size is deliberate -- upstream is 40% of research.context_char_budget --
    so the question is only which 60k, and the answer is: the front and the back,
    never a head prefix. A section too large for the room left is clipped rather
    than dropped, because a radar's opening section is usually the point, and
    skipping it outright once wasted 88% of the budget.
    """
    if len(text) <= limit:
        return text

    where = path or "上游文档对应文件"
    preamble, sections = _split_sections(text)
    # The preamble and the omission note are both emitted outside the section
    # budget, so both have to come out of it or the result overruns the share.
    budget = max(400, limit - _NOTE_ALLOWANCE - len(preamble))
    if not sections:
        # No headings to preserve. Keep both ends anyway; the tail of an
        # unstructured document is still more useful than nothing.
        head = max(1, int(budget * 0.65))
        tail = budget - head
        if tail <= 0:
            return text[:limit]
        return (text[:head]
                + _OMITTED_NOTE.format(omitted=len(text) - head - tail, path=where)
                + text[-tail:])

    # Front gets a slight majority: the opening usually states the round's scope
    # and the highest-signal items, which the later sections refer back to.
    head_budget = int(budget * 0.6)
    taken: set[int] = set()
    head_parts: list[str] = []
    for index, section in enumerate(sections):
        used = sum(len(p) for p in head_parts)
        room = head_budget - used
        if room <= 0:
            break
        if len(section) <= room:
            head_parts.append(section)
        else:
            # Too big for the room left: clip it rather than drop it. A radar's
            # opening section is usually the point, and skipping it outright
            # once left 88% of the budget unspent.
            head_parts.append(_clip(section, room))
            taken.add(index)
            break
        taken.add(index)

    # Then whole sections from the back, kept in their original order. A section
    # that does not fit is skipped rather than ending the walk: one large
    # middle section used to block every smaller section behind it, leaving a
    # tenth of the share unspent.
    tail_parts: list[str] = []
    for index in range(len(sections) - 1, -1, -1):
        if index in taken:
            break
        used = sum(len(p) for p in head_parts + tail_parts)
        room = budget - used
        if room <= 0:
            break
        section = sections[index]
        if len(section) > room:
            continue
        tail_parts.insert(0, section)
        taken.add(index)

    kept = sum(len(p) for p in head_parts + tail_parts)
    omitted = len(text) - len(preamble) - kept
    parts = ([preamble] if preamble.strip() else []) + head_parts
    if omitted > 0:
        parts.append(_OMITTED_NOTE.format(omitted=omitted, path=where))
    parts.extend(tail_parts)
    return "\n\n".join(parts)


_OMITTED_NOTE = (
    "\n\n（本节中间省略了约 {omitted} 字符：为了控制上下文长度，"
    "这里只保留了本文件的开头与结尾。完整原文见 {path}。）\n\n"
)
# Worst-case rendered length of _OMITTED_NOTE with a realistic path, reserved up
# front so the result still honours the caller's limit.
_NOTE_ALLOWANCE = 240


def stage_round_dir(job: dict, date: str) -> str:
    """Where a stage says it will write, relative to the workspace.

    The runner must not re-derive the round's date: a backfill writes to a
    past date, an override can move it again, and getting it wrong means
    reading one round's radars while writing into another's. The stage's output
    template already encodes the answer, so ask it.

    Returns "" for an output path that is not a round directory, so a job with
    a flat ``output/{date}_name.md`` is never mistaken for a pipeline stage.
    """
    template = str((job or {}).get("output") or "")
    if not template:
        return ""
    rendered = template.replace("{date}", date)
    if PIPELINE_DIR not in rendered:
        return ""
    parent = os.path.dirname(rendered)
    return parent if os.path.basename(parent) else ""


def upstream_for(key: str, job: dict, date: str, root: str = ".") -> str:
    """The ``{upstream_reports}`` value for a stage, honest in both directions.

    ``key`` is passed in rather than derived from the job: the caller has
    already resolved it, and a second derivation that quietly disagreed would
    make this return "" -- the same silence it exists to end.

    ``root`` is the workspace the output template is relative to. Passing it
    explicitly matters: the template yields ``output/...``, and resolving that
    against the process CWD would read a different round whenever the runner
    was started from somewhere else.

    Returns a real block when the documents are there, and an explicit
    explanation when they are not. The prompts state that the upstream material
    is attached, so substituting nothing would leave the model reading a claim
    about a section that is not in its prompt -- which is what the daily round
    did for as long as this only ran from the Web console.
    """
    if not STAGE_DEPS.get(key):
        return ""
    relative = stage_round_dir(job, date)
    if not relative:
        return ""
    round_dir = os.path.join(root, relative) if root else relative
    if not os.path.isdir(round_dir):
        return (f"（本轮上游文档不可用：找不到目录 {relative}。"
                f"请在最终报告中说明这一项没有输入，不要凭空补齐。）")
    text, notes = collect_upstream(key, round_dir)
    if text:
        return text
    detail = "；".join(notes) if notes else "目录存在但没有可用的上游文档"
    return (f"（本轮上游文档不可用：{detail}。"
            f"请在最终报告中说明这一项没有输入，不要凭空补齐。）")


def collect_upstream(key: str, round_dir: str) -> tuple[str, list[str]]:
    """Gather the documents ``key`` depends on, as (text, notes).

    STAGE_DEPS already declared these relationships, but they were only used
    for ordering and retry invalidation: nothing ever passed the documents on.
    The model was expected to notice the prompt said "read the radar documents",
    guess the dated path, and spend its context budget rediscovering them --
    which it did not always get right (one round read a June plan in September).
    """
    deps = STAGE_DEPS.get(key) or ()
    if not deps:
        return "", []

    blocks: list[str] = []
    notes: list[str] = []
    # Split the budget evenly. First-come-first-served let P1-P5 eat it and
    # dropped P6 entirely, so a synthesis stage silently lost a whole radar.
    share = max(MIN_UPSTREAM_CHARS, UPSTREAM_CHAR_BUDGET // max(1, len(deps)))
    for dep in deps:
        path = os.path.join(round_dir, STAGE_FILE[dep])
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read().strip()
        except OSError:
            notes.append(f"- {STAGE_LABEL.get(dep, dep)}：缺失（未生成或已失败），"
                         "请在最终报告中说明这一项没有输入。")
            continue
        if len(text) < MIN_UPSTREAM_CHARS:
            # A stub is worse than nothing: it would read as a real finding.
            notes.append(f"- {STAGE_LABEL.get(dep, dep)}：内容过短"
                         f"（{len(text)} 字符），已忽略。")
            continue
        original_len = len(text)
        if len(text) > share:
            text = _select_sections(
                text, share,
                path=f"output/{PIPELINE_DIR}/{os.path.basename(round_dir)}/{STAGE_FILE[dep]}")
        blocks.append(
            f"### {STAGE_LABEL.get(dep, dep)}（{STAGE_FILE[dep]}，原文 {original_len} 字符）"
            f"\n\n{text}"
        )

    if not blocks:
        return "", notes
    header = ("以下是本轮上游阶段已生成的全部文档。它们是本次任务的输入材料，"
              "请直接基于它们工作，不要再去搜索同样的内容。\n")
    if notes:
        header += "\n输入完整性提示：\n" + "\n".join(notes) + "\n"
    return "\n\n---\n\n".join([header] + blocks), notes
