"""Round item tracking routes (actions / tests / watchlist)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core import tracking
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.settings import get_settings

router = APIRouter(prefix="/api/pipeline", tags=["tracking"])


def ensure_tracking_db() -> None:
    tracking.use_state_dir(get_settings().paths.state_dir)


def ensure_events_db() -> None:
    from core import events

    events.use_state_dir(get_settings().paths.state_dir)


@router.get("/actions")
def list_actions(
    kind: str | None = Query(None, pattern="^(action|test|watch)$"),
    item_status: str | None = Query(None, alias="status", pattern="^(open|done|dropped)$"),
    limit: int = Query(500, ge=1, le=2000),
    user=Depends(require_viewer),
):
    """Tracked items across rounds, plus the carry-over classification."""
    ensure_tracking_db()
    return {
        "items": tracking.list_items(kind=kind, status=item_status, limit=limit),
        "carry_over": tracking.carry_over(),
    }


@router.get("/events")
def list_events(
    days: int = Query(7, ge=1, le=90),
    limit: int = Query(50, ge=1, le=500),
    user=Depends(require_viewer),
):
    """Cross-round event memory: sources already reported, plus the prompt block."""
    from core import events

    ensure_events_db()
    return {
        "events": events.recent_events(days=days, limit=limit),
        "prompt_block": events.reported_block(days=days, limit=min(limit, 20)),
        "stats": events.stats(),
    }


@router.get("/outcomes")
def list_outcomes(
    limit: int = Query(20, ge=1, le=200),
    days: int = Query(30, ge=1, le=365),
    user=Depends(require_viewer),
):
    """Recent done/dropped decisions, used to feed the next round's prompt."""
    ensure_tracking_db()
    return {
        "outcomes": tracking.recent_outcomes(limit=limit, days=days),
        "prompt_block": tracking.format_outcomes(limit=min(limit, 20), days=days),
    }


@router.patch("/actions/{item_id}")
def update_action(item_id: str, payload: dict, request: Request,
                  user=Depends(require_editor)):
    """Update the status and/or note of a tracked item."""
    ensure_tracking_db()
    new_status = payload.get("status")
    note = payload.get("note")
    if new_status is None and note is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("nothing_to_update", "缺少 status 或 note"),
        )
    if new_status is not None and new_status not in tracking.STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_status", "status 必须为 open/done/dropped"),
        )

    existing = tracking.get_item(item_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "条目不存在"),
        )
    tracking.set_status(item_id, new_status or existing["status"], note,
                        decided_by=user["username"])
    audit.log(
        "action_update",
        user=user["username"],
        target=item_id,
        result="success",
        details={"status": new_status or existing["status"], "note": bool(note)},
        ip=request.client.host if request.client else None,
    )
    return tracking.get_item(item_id)
