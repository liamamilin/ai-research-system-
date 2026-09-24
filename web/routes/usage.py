"""Usage/cost routes: aggregated LLM token consumption across job runs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from core.budget import month_spend
from core.config import load_system_config
from core.state import StateManager
from web.deps import require_viewer
from web.settings import get_settings

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/ratings")
def get_ratings(user=Depends(require_viewer)):
    from web.indexer import db as index_db

    return index_db.rating_summary()


@router.get("")
def get_usage(
    days: int = Query(30, ge=0, le=3650),
    month: bool = Query(False, description="restrict to the current calendar month"),
    user=Depends(require_viewer),
):
    """Return token usage totals plus per-day/job/model/user breakdowns.

    ``estimated_cost_usd`` is computed when ``ai.pricing`` is configured in
    system.yaml (``input_per_1m`` / ``output_per_1m``).
    """
    settings = get_settings()
    since = None
    if month:
        import datetime as _dt

        since = _dt.datetime.now().replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%S")
    summary = StateManager.get_usage_summary(days=0 if month else days, since=since)

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
