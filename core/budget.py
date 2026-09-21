"""Monthly budget guardrails for LLM/search spend.

Estimates month-to-date spend from the token usage recorded in job history
and the ``ai.pricing`` table in system.yaml.

Configured via ``system.yaml``::

    budget:
      monthly_usd_limit: 0     # 0 disables the guardrail
      warn_ratio: 0.8          # warn at 80% of the limit
      block_pipeline: true     # refuse to start a round when over budget
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import load_system_config
from .state import StateManager

logger = logging.getLogger(__name__)


def _estimate_cost(totals: dict, pricing: dict) -> float:
    input_price = pricing.get("input_per_1m")
    output_price = pricing.get("output_per_1m")
    if input_price is None and output_price is None:
        return 0.0
    return round(
        (totals.get("prompt_tokens", 0) / 1_000_000) * float(input_price or 0)
        + (totals.get("completion_tokens", 0) / 1_000_000) * float(output_price or 0),
        4,
    )


def month_spend(sys_config: Optional[dict] = None) -> dict:
    """Return month-to-date budget status.

    Keys: ``limit``, ``spent``, ``ratio``, ``warn``, ``exceeded``,
    ``block_pipeline``, ``tokens`` (month-to-date total tokens).
    """
    if sys_config is None:
        sys_config = load_system_config("config")

    cfg = sys_config.get("budget", {}) or {}
    limit = float(cfg.get("monthly_usd_limit", 0) or 0)
    warn_ratio = float(cfg.get("warn_ratio", 0.8) or 0.8)

    days = max(1, datetime.now().day)
    summary = StateManager.get_usage_summary(days=days)
    pricing = (sys_config.get("ai") or {}).get("pricing") or {}
    spent = _estimate_cost(summary["totals"], pricing)

    ratio = (spent / limit) if limit > 0 else 0.0
    return {
        "limit": limit,
        "spent": spent,
        "ratio": round(ratio, 4),
        "warn": bool(limit > 0 and ratio >= warn_ratio),
        "exceeded": bool(limit > 0 and spent >= limit),
        "block_pipeline": bool(cfg.get("block_pipeline", True)),
        "tokens": summary["totals"].get("total_tokens", 0),
        "has_pricing": (pricing.get("input_per_1m") is not None
                        or pricing.get("output_per_1m") is not None),
    }


def pipeline_allowed(sys_config: Optional[dict] = None) -> tuple[bool, str]:
    """Whether a pipeline round may start. Returns (allowed, reason)."""
    status = month_spend(sys_config)
    if status["limit"] <= 0 or not status["exceeded"]:
        return True, ""
    if not status["block_pipeline"]:
        logger.warning("Monthly budget exceeded (%.2f/%.2f USD) but "
                       "block_pipeline is disabled", status["spent"], status["limit"])
        return True, ""
    reason = (f"月度预算已用完（${status['spent']:.2f}/${status['limit']:.2f}），"
              f"已阻止运行轮次。请调整 budget.monthly_usd_limit 或充值")
    return False, reason
