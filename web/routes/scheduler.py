"""A unified schedule view; cron editing and read-only calendar previews."""

from __future__ import annotations

import os
import shlex
import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool, StrictStr, StrictInt

from core import cron
from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services import launchd_agents
from web.services import scheduler, managed_scheduler as managed
from core.schedule_store import ScheduleConflict
from core import schedule_host
from web.settings import get_settings

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class ScheduleEdit(BaseModel):
    schedule: StrictStr
    command: StrictStr = ''
    job_name: StrictStr = ''
    timezone: StrictStr = 'local'
    missed_policy: StrictStr = 'latest'
    max_retries: StrictInt = 2


class ScheduleCreate(ScheduleEdit):
    id: StrictStr = ''


class ScheduleToggle(BaseModel):
    enabled: StrictBool


def _error(code: int, kind: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=ApiError.make(kind, message))


def _mutate(operation, *args):
    try:
        return operation(*args)
    except (scheduler.ScheduleConflict, ScheduleConflict) as exc:
        raise _error(409, "conflict", str(exc)) from exc
    except KeyError as exc:
        raise _error(404, "not_found", "调度已被删除，请刷新列表") from exc
    except ValueError as exc:
        raise _error(422, "invalid_schedule", str(exc)) from exc
    except (RuntimeError, OSError, TimeoutError) as exc:
        raise _error(503, "scheduler_error", str(exc)) from exc


def _is_system_job(job_id: str) -> bool:
    """The two pipeline agents, which this page can now edit like any plan."""
    return job_id in launchd_agents.KINDS


def _require_installed(job_id: str) -> None:
    """Refuse to edit an agent that is not on disk, and say how to get it back."""
    if not launchd_agents.read_config(job_id):
        raise _error(409, "not_installed",
                     f"系统任务未安装：{job_id}，请运行 bash scripts/install_launchd.sh")


@router.get("")
def list_scheduled(user=Depends(require_admin)):
    """Use the same discovery and health source as the system health page."""
    warnings = []
    try:
        jobs = scheduler.all_jobs()
        health = scheduler.schedule_health(
            state_dir=get_settings().paths.state_dir, repo_dir=os.getcwd(), jobs=jobs)
        displayed = [scheduler.describe_job(job) for job in jobs]
    except (RuntimeError, OSError) as exc:
        jobs, displayed = [], []
        health = {'jobs': [], 'overdue': 0, 'paused': 0}
        warnings.append(f'旧系统调度读取失败：{exc}')
    warnings.extend(dict.fromkeys(j["discovery_warning"] for j in jobs if j.get("discovery_warning")))
    if health.get("orphan_headers"):
        warnings.append(health["detail"])
    host = schedule_host.status(managed.runtime())
    plans = [managed.describe(plan) for plan in managed.store().list()]
    states = [managed.classify(plan, host) for plan in plans]
    displayed = plans + displayed
    health['jobs'] = states + health['jobs']
    health['overdue'] = sum(s['status'] == 'overdue' for s in health['jobs'])
    health['paused'] = sum(s['status'] == 'paused' for s in health['jobs'])
    health['status'] = 'error' if health['overdue'] else 'warn' if warnings else 'ok'
    removed = launchd_agents.list_deleted(get_settings().paths.state_dir)
    return {"jobs": displayed, "total": len(displayed), "health": health,
            "timezone": cron.timezone_label(), "warnings": warnings,
            'dispatcher': host, "removed_system_jobs": removed}


@router.get("/preview")
def preview_schedule(schedule: str, timezone: str = 'local', user=Depends(require_admin)):
    """Validate a schedule and preview three runs without writing the crontab."""
    try:
        return managed.preview(schedule, timezone)
    except ValueError as exc:
        raise _error(422, "invalid_schedule", str(exc)) from exc


@router.get("/jobs")
def list_schedulable_jobs(user=Depends(require_admin)):
    """List real jobs with quoted commands using the active Python runtime."""
    from core.config import list_jobs, load_job

    settings = get_settings()
    project_dir = os.path.dirname(os.path.abspath(settings.paths.config_dir))
    python = sys.executable

    def quote(value: str) -> str:
        # Cron interprets % before the shell, including inside quoted strings.
        return shlex.quote(value).replace("%", r"\%")

    items = []
    existing = {p['job_name']: p['id'] for p in managed.store().list()}
    for name in list_jobs(settings.paths.jobs_dir):
        job = load_job(settings.paths.jobs_dir, name) or {}
        spec = job.get("schedule") or {}
        spec = spec if isinstance(spec, dict) else {}
        items.append({
            "name": name, "label": job.get("name") or name,
            "description": job.get("description") or "",
            "enabled": bool(job.get("enabled", True)),
            "schedule_type": spec.get("type", "manual"),
            "schedule_id": existing.get(name),
            "command": f"cd {quote(project_dir)} && {quote(python)} run.py {quote(name)} "
                       f">> {quote('logs/cron_' + name.replace('/', '_') + '.log')} 2>&1",
        })
    return {"jobs": items, "project_dir": project_dir, "python": python}


@router.post("")
def add_scheduled(payload: ScheduleCreate, user=Depends(require_admin)):
    """Every new job plan goes to the durable dispatcher, never to cron."""
    job_id = payload.id.strip()
    if not payload.job_name.strip():
        raise _error(422, 'missing_job', '请选择要周期运行的研究任务')
    created = _mutate(lambda: managed.save(
        payload.job_name.strip(), payload.schedule.strip(), schedule_id=job_id or None,
        timezone=payload.timezone, missed_policy=payload.missed_policy, max_retries=payload.max_retries))
    audit.log("schedule_add", user=user["username"], target=job_id)
    return created


@router.put("/{job_id}")
def edit_scheduled(job_id: str, payload: ScheduleEdit, user=Depends(require_admin)):
    """Update a schedule without resetting its paused state."""
    if _is_system_job(job_id):
        _require_installed(job_id)
        config = _mutate(launchd_agents.apply_cron, job_id, payload.schedule)
        audit.log("schedule_edit", user=user["username"], target=job_id,
                  details={"backend": "launchd"})
        return {"ok": True, "backend": "launchd",
                "schedule": launchd_agents.to_cron(job_id, config)}
    plan = managed.store().get(job_id)
    if plan:
        updated = _mutate(lambda: managed.save(
            plan['job_name'], payload.schedule.strip(), schedule_id=job_id, editing=True,
            timezone=payload.timezone, missed_policy=payload.missed_policy, max_retries=payload.max_retries))
    else:
        updated = _mutate(scheduler.update_job, job_id, payload.schedule.strip(), payload.command.strip())
    if updated is None:
        raise _error(404, "not_found", f"未找到调度：{job_id}")
    audit.log("schedule_edit", user=user["username"], target=job_id)
    return updated


@router.delete("/{job_id}")
def remove_scheduled(job_id: str, user=Depends(require_admin)):
    """Remove one schedule, preserving all unrelated entries.

    System agents are moved into ``state/scheduler_backup/`` rather than
    unlinked, so ``POST /{job_id}/restore`` can bring them back.
    """
    if _is_system_job(job_id):
        _require_installed(job_id)
        state_dir = get_settings().paths.state_dir
        result = _mutate(launchd_agents.delete, job_id, state_dir)
        audit.log("schedule_remove", user=user["username"], target=job_id,
                  details={"backend": "launchd", "backup": result["backup"]})
        return {"ok": True, "backend": "launchd", "restorable": True, **result}
    operation = managed.store().delete if managed.store().get(job_id) else scheduler.remove_job
    if not _mutate(operation, job_id):
        raise _error(404, "not_found", f"未找到调度：{job_id}")
    audit.log("schedule_remove", user=user["username"], target=job_id)
    return {"ok": True}


@router.post("/{job_id}/restore")
def restore_scheduled(job_id: str, user=Depends(require_admin)):
    """Put a deleted system agent back and load it again."""
    if not _is_system_job(job_id):
        raise _error(400, "not_restorable", "只有情报矩阵系统任务支持恢复")
    state_dir = get_settings().paths.state_dir
    result = _mutate(launchd_agents.restore, job_id, state_dir)
    audit.log("schedule_restore", user=user["username"], target=job_id,
              details={"backend": "launchd"})
    return {"ok": True, **result}


@router.put("/{job_id}/toggle")
def toggle_scheduled(job_id: str, payload: ScheduleToggle, user=Depends(require_admin)):
    """Set enabled state idempotently."""
    if _is_system_job(job_id):
        _require_installed(job_id)
        # Pausing unloads the agent but leaves the plist, so the configured
        # trigger survives and enabling cannot silently lose it.
        _mutate(launchd_agents.set_enabled, job_id, payload.enabled)
        audit.log("schedule_toggle", user=user["username"], target=job_id,
                  details={"backend": "launchd"},
                  result="enabled" if payload.enabled else "disabled")
        return {"ok": True, "enabled": payload.enabled}
    plan = managed.store().get(job_id)
    if plan and payload.enabled:
        _mutate(managed.ensure_online)
    operation = managed.store().toggle if plan else scheduler.toggle_job
    if not _mutate(operation, job_id, payload.enabled):
        raise _error(404, "not_found", f"未找到调度：{job_id}")
    audit.log("schedule_toggle", user=user["username"], target=job_id,
              result="enabled" if payload.enabled else "disabled")
    return {"ok": True, "enabled": payload.enabled}


@router.post('/dispatcher/start')
def start_dispatcher(user=Depends(require_admin)):
    """Install/recover the host and wait for an actual heartbeat."""
    result = _mutate(managed.ensure_online)
    audit.log('scheduler_host_start', user=user['username'])
    return result


@router.get('/{job_id}/runs')
def execution_history(job_id: str, user=Depends(require_admin)):
    """Execution records survive restarts and plan deletion."""
    storage = managed.store()
    plan = storage.get(job_id)
    from core.schedule_store import iso

    def stamp(run: dict, field: str) -> str | None:
        # The run carries the zone it was scheduled in. After the plan is gone
        # there is nothing left to ask, and falling back to the server's local
        # zone would shift every timestamp by the difference.
        timezone = run.get('timezone') or (plan['timezone'] if plan else 'local')
        return iso(run[field], timezone)

    return {'runs': [{**run, 'due_at_iso': stamp(run, 'due_at'),
                      'started_at_iso': stamp(run, 'started_at')}
                     for run in storage.runs(job_id)]}
