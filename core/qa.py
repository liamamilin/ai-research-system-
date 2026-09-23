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
        })
    return citations


def build_messages(question: str, hits: list[dict]) -> list[dict]:
    """Build chat messages with numbered context chunks."""
    parts: list[str] = []
    used = 0
    for i, hit in enumerate(hits, 1):
        snippet = (hit.get("snippet") or "").strip()
        if not snippet:
            continue
        block = f"[{i}] 来源：{hit.get('path', '')}\n{snippet}"
        if used + len(block) > _MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)

    context = "\n\n".join(parts) if parts else "（没有检索到相关资料）"
    return [
        {"role": "system", "content": _SYSTEM},
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
