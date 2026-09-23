"""Pipeline round routes: status board + one-click run/cancel."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core import artifacts, report_meta
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.runner import pipeline
from web.settings import get_settings

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _round_artifacts(date: str, output_dir: str) -> list[dict]:
    """Existing JSON artifacts of a round (name + size), for downloads."""
    round_dir = os.path.join(output_dir, pipeline.PIPELINE_DIR, date)
    found = []
    for name in (artifacts.ACTION_FILE, artifacts.WATCHLIST_FILE, artifacts.SOURCES_FILE):
        path = os.path.join(round_dir, name)
        if os.path.isfile(path):
            found.append({"name": name, "size": os.path.getsize(path)})
    return found


def _round_payload(date: str) -> dict:
    settings = get_settings()
    stages = pipeline.build_round_files(date, settings.paths.output_dir)
    tokens = report_meta.round_tokens(date)
    for stage in stages:
        stage["tokens"] = tokens.get(stage["key"], 0)
    done = sum(1 for s in stages if s["exists"])
    return {
        "date": date,
        "done": done,
        "total": len(stages),
        "tokens_total": sum(tokens.values()),
        "stages": stages,
        "artifacts": _round_artifacts(date, settings.paths.output_dir),
        "live": pipeline.get_round_state(date),
    }


@router.get("/rounds")
def list_rounds(
    limit: int = Query(14, ge=1, le=120),
    user=Depends(require_viewer),
):
    """List recent rounds with per-stage output status."""
    settings = get_settings()
    base = os.path.join(settings.paths.output_dir, pipeline.PIPELINE_DIR)

    dates: list[str] = []
    if os.path.isdir(base):
        dates = sorted(
            (d for d in os.listdir(base)
             if os.path.isdir(os.path.join(base, d)) and len(d) == 10),
            reverse=True,
        )

    # Make sure a currently running round is always visible
    for state in pipeline.list_round_states():
        if state["status"] == "running" and state["date"] not in dates:
            dates.insert(0, state["date"])

    rounds = [_round_payload(d) for d in dates[:limit]]
    running = any(
        r["live"] and r["live"]["status"] == "running" for r in rounds
    )
    return {
        "rounds": rounds,
        "running": running,
        "groups": [{"name": name, "stages": keys} for name, keys in pipeline.GROUPS],
    }


@router.get("/rounds/{date}")
def get_round(date: str, user=Depends(require_viewer)):
    if len(date) != 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_date", "日期格式应为 YYYY-MM-DD"),
        )
    return _round_payload(date)


@router.get("/rounds/{date}/diff")
def get_round_diff(date: str, against: str | None = Query(None),
                   user=Depends(require_viewer)):
    """Content diff between a round and the previous one (or ``against``)."""
    if len(date) != 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_date", "日期格式应为 YYYY-MM-DD"),
        )
    settings = get_settings()
    base = os.path.join(settings.paths.output_dir, pipeline.PIPELINE_DIR)
    round_b = os.path.join(base, date)
    if not os.path.isdir(round_b):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("round_not_found", "轮次不存在"),
        )

    if against:
        if len(against) != 10:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_date", "against 格式应为 YYYY-MM-DD"),
            )
        previous = against
    else:
        candidates = sorted(
            d for d in os.listdir(base)
            if len(d) == 10 and os.path.isdir(os.path.join(base, d)) and d < date
        )
        previous = candidates[-1] if candidates else None

    if not previous:
        return {"from": None, "to": date, "actions": {"added": [], "removed": [], "persisted": []},
                "tests": {"added": [], "removed": [], "persisted": []},
                "watchlist": {"added": [], "removed": [], "persisted": []},
                "sources": {"added": [], "removed": [], "new_domains": []},
                "counts": {"actions_added": 0, "actions_removed": 0, "tests_added": 0,
                           "watchlist_added": 0, "watchlist_removed": 0,
                           "sources_added": 0, "sources_removed": 0}}

    diff = artifacts.diff_rounds(os.path.join(base, previous), round_b,
                                 date_a=previous, date_b=date)
    if diff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("round_not_found", "对比轮次不存在"),
        )
    return diff


@router.post("/run")
def run_round(request: Request, payload: dict | None = None,
              user=Depends(require_editor)):
    """Start today's round in the background."""
    settings = get_settings()
    payload = payload or {}
    try:
        concurrency = int(payload.get("concurrency", 3))
    except (TypeError, ValueError):
        concurrency = 3

    try:
        state = pipeline.start_round(
            config_dir=settings.paths.config_dir,
            jobs_dir=settings.paths.jobs_dir,
            concurrency=concurrency,
            trigger=user["username"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("already_running", str(e)),
        )

    audit.log("pipeline_run", user=user["username"], target=state["date"],
              result="started",
              ip=request.client.host if request.client else None)
    return state


@router.post("/retry")
def retry_round(request: Request, payload: dict | None = None,
                user=Depends(require_editor)):
    """Re-run the unfinished stages of today's (or a given) round."""
    settings = get_settings()
    payload = payload or {}
    try:
        concurrency = int(payload.get("concurrency", 3))
    except (TypeError, ValueError):
        concurrency = 3
    date = payload.get("date")

    try:
        state = pipeline.start_retry(
            config_dir=settings.paths.config_dir,
            jobs_dir=settings.paths.jobs_dir,
            output_dir=settings.paths.output_dir,
            concurrency=concurrency,
            trigger=user["username"],
            date=date,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("retry_unavailable", str(e)),
        )

    audit.log("pipeline_retry", user=user["username"], target=state["date"],
              result="started",
              ip=request.client.host if request.client else None)
    return state


@router.post("/cancel")
def cancel_round(request: Request, user=Depends(require_editor)):
    """Cancel the running round (stops in-flight stages)."""
    if not pipeline.cancel_round():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_running", "当前没有正在运行的轮次"),
        )
    audit.log("pipeline_cancel", user=user["username"], result="cancelled",
              ip=request.client.host if request.client else None)
    return {"ok": True}
