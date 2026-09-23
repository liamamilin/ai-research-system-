"""Round digest text for notifications.

Turns the structured artifacts of a round into a short push-friendly
summary: status, top actions by priority, watchlist size and failed stages.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from .artifacts import load_round_payloads

logger = logging.getLogger(__name__)

_MAX_LINE = 120


def _priority_rank(value: str) -> tuple:
    clean = re.sub(r"[^A-Za-z0-9]", "", value or "").upper()
    match = re.match(r"P(\d+)", clean)
    if match:
        return (0, int(match.group(1)))
    return (1, 99)


def _clip(text: str, limit: int = _MAX_LINE) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_digest(date: str, round_dir: str,
                 results: Optional[dict] = None,
                 max_actions: int = 5) -> Optional[dict]:
    """Build ``{title, text}`` for a round, or None when there is no data."""
    payloads = load_round_payloads(round_dir)
    if not payloads:
        return None

    actions = payloads["actions"].get("actions") or []
    tests = payloads["actions"].get("tests") or []
    watchlist = payloads["watchlist"].get("items") or []
    sources = payloads["sources"].get("sources") or []

    if not actions and not watchlist and not sources:
        return None

    lines: list[str] = []
    if results:
        failed = [k for k, v in results.items() if v != "success"]
        done = len(results) - len(failed)
        status_line = f"完成 {done}/{len(results)}"
        if failed:
            status_line += " · 失败：" + "、".join(failed)
        lines.append(status_line)

    ranked = sorted(actions, key=lambda a: _priority_rank(a.get("priority", "")))
    top = ranked[:max(1, int(max_actions))]
    if top:
        lines.append(f"Top 行动（{len(actions)} 项）：")
        for i, item in enumerate(top, 1):
            priority = re.sub(r"[^A-Za-z0-9]", "", item.get("priority", "") or "")
            prefix = f"[{priority}] " if priority else ""
            lines.append(f"{i}. {prefix}{_clip(item.get('action', ''))}")

    if tests:
        lines.append(f"本周测试 {len(tests)} 项")
    if watchlist:
        lines.append(f"观察清单 {len(watchlist)} 条")
    if sources:
        lines.append(f"来源 {len(sources)} 个")

    text = "\n".join(lines)
    if not text:
        return None
    return {"title": f"情报轮次 {date}", "text": text}


def digest_for_round(date: str, output_dir: str,
                     pipeline_dir: str = "practical_ai_intelligence",
                     results: Optional[dict] = None,
                     max_actions: int = 5) -> Optional[dict]:
    """Convenience wrapper resolving the round directory from ``output_dir``."""
    round_dir = os.path.join(output_dir, pipeline_dir, date)
    return build_digest(date, round_dir, results=results, max_actions=max_actions)
