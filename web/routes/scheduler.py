"""Scheduler routes: manage cron jobs from the web UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services import scheduler

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


@router.get("")
def list_scheduled(user=Depends(require_admin)):
    """List all scheduled cron jobs."""
    try:
        jobs = scheduler.list_jobs()
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ApiError.make("scheduler_error", str(e)),
        )
    return {"jobs": jobs, "total": len(jobs)}


@router.post("")
def add_scheduled(payload: dict, user=Depends(require_admin)):
    """Add a new scheduled cron job."""
    job_id = payload.get("id", "").strip()
    schedule = payload.get("schedule", "").strip()
    command = payload.get("command", "").strip()

    if not job_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_field", "缺少 id"),
        )
    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_field", "缺少 schedule"),
        )
    if not command:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_field", "缺少 command"),
        )

    try:
        created = scheduler.add_job(job_id, schedule, command)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("conflict", str(e)),
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ApiError.make("scheduler_error", str(e)),
        )

    audit.log("schedule_add", user=user["username"], target=job_id,
              result="success")
    return created


@router.delete("/{job_id}")
def remove_scheduled(job_id: str, user=Depends(require_admin)):
    """Remove a scheduled job."""
    removed = scheduler.remove_job(job_id)
    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", f"未找到 schedule: {job_id}"),
        )

    audit.log("schedule_remove", user=user["username"], target=job_id,
              result="success")
    return {"ok": True}


@router.put("/{job_id}/toggle")
def toggle_scheduled(job_id: str, payload: dict, user=Depends(require_admin)):
    """Enable or disable a scheduled job."""
    enabled = payload.get("enabled", True)
    toggled = scheduler.toggle_job(job_id, enabled=enabled)
    if not toggled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", f"未找到 schedule: {job_id}"),
        )

    audit.log("schedule_toggle", user=user["username"], target=job_id,
              result="enabled" if enabled else "disabled")
    return {"ok": True, "enabled": enabled}
