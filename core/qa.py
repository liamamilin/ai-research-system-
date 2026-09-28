"""Retrieval-augmented Q&A over generated reports."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是 AI Research Console 的研究助理。只能依据提供的资料片段回答问题；"
    "资料不足时明确说明缺少什么，不要编造。回答使用中文，结构清晰，"
    "并在相关句子后标注引用编号，如 [1]、[2]。"
)

_MAX_CONTEXT_CHARS = 12000


def format_citations(hits: list[dict]) -> list[dict]:
    """Numbered citation entries for the UI and the prompt."""
    citations = []
    for i, hit in enumerate(hits, 1):
        citations.append({
            "index": i,
            "path": hit.get("path", ""),
            "title": hit.get("title") or hit.get("path", "").split("/")[-1],
            "snippet": (hit.get("snippet") or "")[:400],
            "source": hit.get("source", ""),
            "score": round(float(hit.get("score") or 0), 4),
            "report_date": hit.get("report_date") or "",
        })
    return citations


def stage_legend(hits: list[dict]) -> str:
    """Map the pipeline's stage numbers to the files the user is being shown.

    People ask about "P7" and "P9" because that is how the pipeline names its
    stages, and the number prefix of each report is the only place that mapping
    exists -- the reports themselves never say "P9". Asked for P9's immediate
    actions, the model was handed the right table, could not tell which file it
    came from, and said so: "无法确认 P9 指代什么".

    Built from the paths actually retrieved, so it cannot drift from the corpus
    and it does not pad the prompt with stages nobody asked about.
    """
    pairs: dict[str, str] = {}
    for hit in hits:
        name = (hit.get("path") or "").rsplit("/", 1)[-1]
        if not name or "_" not in name:
            continue
        number, _, rest = name.partition("_")
        # 00 is the collection plan, not a stage: the pipeline numbers its
        # stages 01..09 and calls them P1..P9. Calling it P0 was worse than
        # useless -- asked about P1, the model read the legend, saw P0, P7, P9
        # and no P1, and concluded P1 did not exist.
        if not number.isdigit() or not rest or int(number) == 0:
            continue
        label = rest[:-3] if rest.endswith(".md") else rest
        if label and number not in pairs:
            pairs[number] = label
    if not pairs:
        return ""
    listed = "，".join(f"P{int(n)}={pairs[n]}" for n in sorted(pairs, key=int))
    return (
        f"本次检索到的文件与阶段编号对应：{listed}。"
        "这只是本次召回的子集，编号里没出现的阶段不代表不存在；"
        "若用户问的阶段不在其中，请先按文件名和内容判断，不要直接断言该阶段缺失。"
    )


def build_messages(question: str, hits: list[dict]) -> list[dict]:
    """Build chat messages with numbered context chunks."""
    parts: list[str] = []
    used = 0
    for i, hit in enumerate(hits, 1):
        snippet = (hit.get("snippet") or "").strip()
        if not snippet:
            continue
        # The date travels with the excerpt: without it the model cannot tell a
        # June finding from yesterday's, and happily presents stale news as
        # current.
        stamp = hit.get("report_date") or ""
        source = f"{hit.get('path', '')}（{stamp}）" if stamp else hit.get("path", "")
        block = f"[{i}] 来源：{source}\n{snippet}"
        if used + len(block) > _MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)

    context = "\n\n".join(parts) if parts else "（没有检索到相关资料）"
    system = _SYSTEM
    legend = stage_legend(hits)
    if legend:
        system = f"{_SYSTEM}\n\n{legend}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"资料片段：\n\n{context}\n\n问题：{question}"},
    ]


def ask(question: str, hits: list[dict], llm) -> dict:
    """Answer a question with the given retrieval hits via ``llm.chat``."""
    messages = build_messages(question, hits)
    response = llm.chat(messages, temperature=0.2)
    answer = (getattr(response, "content", "") or "").strip()
    return {
        "answer": answer,
        "citations": format_citations(hits),
        "usage": getattr(response, "usage", {}) or {},
    }


def ask_with_fallback(question: str, hits: list[dict],
                      llm_factory) -> Optional[dict]:
    """Best-effort wrapper: returns None when the LLM call fails."""
    try:
        llm = llm_factory()
        try:
            return ask(question, hits, llm)
        finally:
            close = getattr(llm, "close", None)
            if callable(close):
                close()
    except Exception as exc:  # noqa: BLE001 - QA is best-effort
        logger.warning("QA answer failed: %s", str(exc)[:200])
        return None
