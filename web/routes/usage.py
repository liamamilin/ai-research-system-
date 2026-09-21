"""Usage/cost routes: aggregated LLM token consumption across job runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from core.budget import month_spend
from core.config import load_system_config
from core.state import StateManager
from web.deps import require_viewer
from web.settings import get_settings

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("")
def get_usage(
    days: int = Query(30, ge=0, le=3650),
    user=Depends(require_viewer),
):
    """Return token usage totals plus per-day/job/model breakdowns.

    ``estimated_cost_usd`` is computed when ``ai.pricing`` is configured in
    system.yaml (``input_per_1m`` / ``output_per_1m``).
    """
    settings = get_settings()
    summary = StateManager.get_usage_summary(days=days)

    sys_cfg = load_system_config(settings.paths.config_dir)
    pricing = (sys_cfg.get("ai") or {}).get("pricing") or {}
    input_price = pricing.get("input_per_1m")
    output_price = pricing.get("output_per_1m")

    estimate = None
    if input_price is not None or output_price is not None:
        totals = summary["totals"]
        estimate = round(
            (totals["prompt_tokens"] / 1_000_000) * float(input_price or 0)
            + (totals["completion_tokens"] / 1_000_000) * float(output_price or 0),
            4,
        )

    summary["estimated_cost_usd"] = estimate
    summary["pricing"] = {
        "input_per_1m": input_price,
        "output_per_1m": output_price,
    }
    summary["budget"] = month_spend(sys_cfg)
    return summary
