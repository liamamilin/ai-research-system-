"""Job-related routes (M1+M3: read-only, edit, run, cancel, SSE stream)."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from core.config import list_jobs, load_job
from core.state import StateManager
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError, JobDetail, JobState, JobSummary, JobYamlUpdate
from web.runner.executor import start_job
from web.runner.registry import TaskRegistry
from web.services import yaml_io
from web.settings import get_settings

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_registry = TaskRegistry()


def _category_from_name(name: str) -> str:
    parts = name.split("/", 1)
    return parts[0] if len(parts) > 1 else ""


def _state_for(job_name: str) -> Optional[JobState]:
    raw = StateManager.get(job_name)
    if not raw:
        return None
    return JobState(**{k: raw.get(k) for k in JobState.model_fields})


def _summary(job: dict) -> JobSummary:
    name = job.get("_file") or job.get("name", "")
    return JobSummary(
        name=name,
        description=job.get("description", ""),
        category=_category_from_name(name),
        enabled=bool(job.get("enabled", True)),
        keywords=list(job.get("keywords", []) or []),
        output_template=job.get("output", ""),
        state=_state_for(name),
        is_running=_registry.is_running(name),
    )


@router.get("", response_model=list[JobSummary])
def list_all(user=Depends(require_viewer)):
    settings = get_settings()
    names = list_jobs(settings.paths.jobs_dir, enabled_only=False)
    result: list[JobSummary] = []
    for name in names:
        job = load_job(settings.paths.jobs_dir, name)
        if not job:
            continue
        result.append(_summary(job))
    return result


@router.get("/templates")
def list_templates(user=Depends(require_viewer)):
    """List available job templates from jobs/_templates/."""
    settings = get_settings()
    tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
    if not os.path.isdir(tmpl_dir):
        return {"templates": []}

    templates = []
    for fn in sorted(os.listdir(tmpl_dir)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        fp = os.path.join(tmpl_dir, fn)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read()
            parsed = yaml_io.parse_yaml(content)
            templates.append({
                "name": fn.replace("_", "").replace(".yaml", "").replace(".yml", ""),
                "filename": fn,
                "description": parsed.get("description", ""),
                "content": content,
            })
        except Exception:
            continue
    return {"templates": templates}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_job(payload: dict, user=Depends(require_editor)):
    """Create a new job file from a template."""
    settings = get_settings()
    name = payload.get("name", "").strip()
    template_name = payload.get("template", "")
    category = payload.get("category", "").strip()
    description = payload.get("description", "").strip()
    language = payload.get("language", "zh")
    keywords = payload.get("keywords", [])
    prompt = payload.get("prompt", "").strip()
    output = payload.get("output", "").strip()

    if not name:
        raise HTTPException(status_code=422, detail=ApiError.make("missing_name", "缺少 job 名称"))

    # Sanitize: prevent path traversal in name and category
    safe_name = re.sub(r"[^a-zA-Z0-9_\-,]", "_", name.replace(" ", "_").lower())
    if category:
        safe_category = re.sub(r"[^a-zA-Z0-9_\-/]", "", category.strip().lower())
        safe_category = os.path.normpath(safe_category).lstrip("/")
        # Prevent traversal via normalized path
        if safe_category.startswith("..") or "/.." in safe_category:
            raise HTTPException(status_code=422, detail=ApiError.make("invalid_category", "分类名称无效"))
    else:
        safe_category = ""

    # Determine directory
    if safe_category:
        jobs_subdir = os.path.join(settings.paths.jobs_dir, safe_category)
    else:
        jobs_subdir = settings.paths.jobs_dir
    os.makedirs(jobs_subdir, exist_ok=True)

    file_path = os.path.join(jobs_subdir, f"{safe_name}.yaml")
    if os.path.isfile(file_path):
        raise HTTPException(status_code=409, detail=ApiError.make("job_exists", f"Job '{safe_name}' 已存在"))

    # If template provided, use it as base
    if template_name:
        tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
        tmpl_path = os.path.join(tmpl_dir, template_name)
        if os.path.isfile(tmpl_path):
            with open(tmpl_path, "r", encoding="utf-8") as f:
                base_content = f.read()
        else:
            # Try without underscore prefix
            alt = os.path.join(tmpl_dir, f"_{template_name}")
            if not os.path.isfile(alt):
                alt = os.path.join(tmpl_dir, f"_{template_name}.yaml")
            if os.path.isfile(alt):
                with open(alt, "r", encoding="utf-8") as f:
                    base_content = f.read()
            else:
                base_content = _default_job_yaml(name, description, language, keywords, prompt, output)
    else:
        base_content = _default_job_yaml(name, description, language, keywords, prompt, output)

    yaml_io.atomic_write(file_path, base_content)

    audit.log("job_create", user=user["username"], target=safe_name, result="success")

    # Load back and return
    job = load_job(settings.paths.jobs_dir, safe_name)
    if job:
        return _summary(job)
    return {"ok": True, "path": file_path}


def _default_job_yaml(name: str, description: str, language: str, keywords: list, prompt: str, output: str) -> str:
    """Generate a default job YAML content."""
    kw = "\n  - " + "\n  - ".join(keywords) if keywords else ""
    out = output or f"output/{{date}}_{name.replace(' ', '_')}.md"
    prompt_text = prompt or "Research {name}. Keywords: {keywords}. Language: {language}.\nGenerate a report in Markdown."
    return f"""name: "{name}"
description: "{description}"
enabled: true
keywords:{kw}
language: "{language}"
prompt: |
  {prompt_text}
output: "{out}"
"""


@router.get("/{name:path}/state", response_model=Optional[JobState])
def get_state(name: str, user=Depends(require_viewer)):
    return _state_for(name)


@router.get("/{name:path}/history")
def get_history(name: str, limit: int = 50, user=Depends(require_viewer)):
    """Return the last N history entries for a job."""
    return StateManager.get_history(name, limit=limit)


@router.post("/{name:path}/run")
def run_job(name: str, request: Request, user=Depends(require_editor)):
    """Trigger a job to run asynchronously in the background."""
    settings = get_settings()
    job = load_job(settings.paths.jobs_dir, name)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("job_not_found", f"未找到 job: {name}"),
        )
    if not job.get("enabled", True):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ApiError.make("job_disabled", f"Job '{name}' 已禁用"),
        )

    if _registry.is_running(name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("already_running", f"Job '{name}' 正在运行中"),
        )

    task = start_job(
        job_name=name,
        started_by=user["username"],
        config_dir=settings.paths.config_dir,
        jobs_dir=settings.paths.jobs_dir,
    )

    audit.log("job_run", user=user["username"], target=name,
              result="started", ip=request.client.host if request.client else None)

    return {
        "ok": True,
        "task_id": task.task_id,
        "started_at": task.started_at,
    }


@router.post("/{name:path}/cancel")
def cancel_job(name: str, request: Request, user=Depends(require_editor)):
    """Cancel a running job."""
    cancelled = _registry.cancel(name)
    if cancelled:
        audit.log("job_cancel", user=user["username"], target=name,
                  result="cancelled", ip=request.client.host if request.client else None)
        return {"ok": True, "cancelled": True}
    else:
        return {"ok": True, "cancelled": False, "message": "没有正在运行的 job"}


@router.get("/{name:path}/stream")
async def stream_job(name: str, request: Request, user=Depends(require_viewer)):
    """SSE endpoint: streams log + status events for a job.

    Supports Last-Event-ID for reconnection.
    """
    task = _registry.get(name)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("no_task", f"没有找到运行记录: {name}"),
        )

    # Get last event id from header for reconnection
    last_id = 0
    if request.headers.get("last-event-id"):
        try:
            last_id = int(request.headers["last-event-id"])
        except (ValueError, TypeError):
            pass

    # Replay events since last_id
    initial_events = task.log_bus.replay(since_id=last_id)

    # If the task is finished and no new events, return a single SSE status event
    if not task.is_running and not initial_events:
        async def final_event():
            yield _sse_format({"type": "status", "status": task.status})

        return StreamingResponse(
            final_event(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    async def event_stream():
        # Flush initial replay
        for event in initial_events:
            yield _sse_format(event)

        # Subscribe for new events
        q = task.log_bus.subscribe()
        try:
            while True:
                event = await q.get()
                yield _sse_format(event)
                if event.get("type") in ("status", "error"):
                    break
        except asyncio.CancelledError:
            pass
        finally:
            task.log_bus.unsubscribe(q)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse_format(event: dict) -> str:
    """Format a dict as an SSE message string."""
    _id = event.get("_id")
    lines = [f"data: {json.dumps(event, ensure_ascii=False)}"]
    if _id is not None:
        lines.insert(0, f"id: {_id}")
    return "\n".join(lines) + "\n\n"


@router.put("/{name:path}", response_model=dict)
def update_yaml(
    name: str,
    payload: JobYamlUpdate,
    user=Depends(require_editor),
):
    """Update a job's YAML configuration. Preserves comments and formatting."""
    settings = get_settings()
    job = load_job(settings.paths.jobs_dir, name)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("job_not_found", f"未找到 job: {name}"),
        )

    resolved = job["_file"]
    file_path = os.path.join(settings.paths.jobs_dir, f"{resolved}.yaml")
    if not os.path.isfile(file_path):
        alt = os.path.join(settings.paths.jobs_dir, f"{resolved}.yml")
        if os.path.isfile(alt):
            file_path = alt
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ApiError.make("file_missing", "job 文件不存在"),
            )

    try:
        result = yaml_io.save_job_yaml(
            file_path=file_path,
            new_content=payload.yaml_content,
            expected_mtime=payload.expected_mtime,
            backup=True,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("yaml_invalid", str(e)),
        )
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("file_missing", str(e)),
        )
    except IOError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("conflict", str(e)),
        )

    audit.log(
        "job_yaml_update",
        user=user["username"],
        target=name,
        result="success" if not result["warnings"] else "warning",
        details={"warnings": result["warnings"], "backup": result.get("backup_path")},
    )

    response = {
        "ok": True,
        "warnings": result["warnings"],
    }
    if result.get("backup_path"):
        response["backup_path"] = result["backup_path"]
    return response


@router.get("/{name:path}", response_model=JobDetail)
def get_one(name: str, user=Depends(require_viewer)):
    settings = get_settings()
    job = load_job(settings.paths.jobs_dir, name)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("job_not_found", f"未找到 job: {name}"),
        )

    resolved = job["_file"]
    file_path = os.path.join(settings.paths.jobs_dir, f"{resolved}.yaml")
    if not os.path.isfile(file_path):
        # try .yml
        alt = os.path.join(settings.paths.jobs_dir, f"{resolved}.yml")
        if os.path.isfile(alt):
            file_path = alt
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ApiError.make("file_missing", "job 文件不存在"),
            )

    with open(file_path, "r", encoding="utf-8") as f:
        yaml_content = f.read()
    mtime = os.path.getmtime(file_path)

    summary = _summary(job)
    return JobDetail(
        **summary.model_dump(),
        yaml_content=yaml_content,
        yaml_mtime=mtime,
        file_path=file_path,
    )


@router.delete("/{name:path}")
def delete_job(name: str, request: Request, user=Depends(require_editor)):
    """Delete a job YAML file. Refuses while the job is running."""
    settings = get_settings()
    jobs_dir = os.path.abspath(settings.paths.jobs_dir)
    job = load_job(settings.paths.jobs_dir, name)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("job_not_found", f"未找到 job: {name}"),
        )
    if _registry.is_running(job.get("_file", name)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("already_running", "Job 正在运行，无法删除"),
        )

    rel = job["_file"]
    deleted_path = None
    for ext in (".yaml", ".yml"):
        candidate = os.path.abspath(os.path.join(jobs_dir, rel + ext))
        if not candidate.startswith(jobs_dir + os.sep):
            continue
        if os.path.isfile(candidate):
            os.remove(candidate)
            deleted_path = candidate
            break

    if not deleted_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("file_not_found", "Job 文件不存在"),
        )

    audit.log("job_delete", user=user["username"], target=rel, result="success",
              ip=request.client.host if request.client else None)
    return {"ok": True, "deleted": os.path.relpath(deleted_path, jobs_dir)}
