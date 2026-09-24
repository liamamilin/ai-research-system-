"""Job-related routes (M1+M3: read-only, edit, run, cancel, SSE stream)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
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

logger = logging.getLogger("ai_research.web.jobs")

_registry = TaskRegistry()

PROMPT_VARIABLES = {
    "name", "keywords", "language", "date", "date_1d_ago", "date_7d_ago",
    "time", "datetime", "recent_outcomes", "reported_events",
}
OUTPUT_VARIABLES = {"name", "date", "time", "datetime"}
TEMPLATE_CATEGORIES = {"monitoring", "research", "analysis", "practice", "actionable"}
SCHEDULE_TYPES = {"manual", "daily", "weekly", "monthly"}


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
        file_mtime=job.get("_mtime") or None,
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


@router.get("/categories")
def list_categories(user=Depends(require_viewer)):
    """Existing job categories (top-level subdirectories) with job counts."""
    settings = get_settings()
    counts: dict[str, int] = {}
    for name in list_jobs(settings.paths.jobs_dir):
        cat = _category_from_name(name)
        if not cat:
            continue
        counts[cat] = counts.get(cat, 0) + 1
    return {
        "categories": [
            {"name": cat, "count": counts[cat]}
            for cat in sorted(counts)
        ]
    }


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
            key = fn.rsplit(".", 1)[0]
            templates.append({
                "key": key,
                "name": key.lstrip("_"),
                "filename": fn,
                "label": str(parsed.get("label") or parsed.get("name") or key),
                "category": str(parsed.get("category") or ""),
                "description": str(parsed.get("description") or ""),
                "builtin": key.startswith("_"),
                "variables": _prompt_variables(parsed),
                "output_template": str(parsed.get("output") or ""),
                "updated_at": _file_mtime(fp),
                **_template_fields(parsed),
                "content": content,
            })
        except Exception:
            continue
    return {"templates": templates}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
def create_template(payload: dict, user=Depends(require_editor)):
    """Create a new reusable job template in jobs/_templates/."""
    settings = get_settings()
    tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
    os.makedirs(tmpl_dir, exist_ok=True)

    data = _validate_template_payload(payload, partial=False)
    key = data["_key"]
    file_path = os.path.join(tmpl_dir, f"{key}.yaml")
    if os.path.isfile(file_path):
        raise HTTPException(status_code=409, detail=ApiError.make("template_exists", f"模板 '{key}' 已存在"))

    file_path = _write_template(tmpl_dir, data)
    audit.log("template_create", user=user["username"], target=key, result="success")
    return _read_template(tmpl_dir, key)


@router.put("/templates/{key}")
def update_template(key: str, payload: dict, user=Depends(require_editor)):
    """Update an existing template. Built-in templates may be edited but not deleted."""
    settings = get_settings()
    tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
    _require_key(key, allow_builtin=True)
    file_path = _template_path(tmpl_dir, key)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=ApiError.make("template_not_found", f"模板 '{key}' 不存在"))

    merged = _validate_template_payload(payload, partial=True, existing=file_path)
    merged["_key"] = key
    yaml_io.save_backup(file_path, _read_text(file_path))
    _write_template(tmpl_dir, merged)
    audit.log("template_update", user=user["username"], target=key, result="success")
    return _read_template(tmpl_dir, key)


@router.delete("/templates/{key}")
def delete_template(key: str, user=Depends(require_editor)):
    """Delete a user-created template. Built-in templates are protected."""
    settings = get_settings()
    tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
    _require_key(key, allow_builtin=False)
    file_path = _template_path(tmpl_dir, key)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail=ApiError.make("template_not_found", f"模板 '{key}' 不存在"))
    os.remove(file_path)
    audit.log("template_delete", user=user["username"], target=key, result="success")
    return {"ok": True, "key": key}


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _file_mtime(path: str) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    except OSError:
        return ""


def _prompt_variables(parsed: dict) -> list[str]:
    prompt = str(parsed.get("prompt") or "")
    return sorted(set(re.findall(r"\{(\w+)\}", prompt)))


def _template_path(tmpl_dir: str, key: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_-]{1,63}", key):
        raise HTTPException(status_code=422, detail=ApiError.make("invalid_template_key", "模板 key 含非法字符"))
    return os.path.join(tmpl_dir, f"{key}.yaml")


def _require_key(key: str, allow_builtin: bool) -> None:
    _template_path("", key)
    if key.startswith("_") and not allow_builtin:
        raise HTTPException(
            status_code=409,
            detail=ApiError.make("builtin_template", "系统内置模板不可删除"),
        )


def _template_data(
    key: str,
    label: str,
    category: str,
    description: str,
    prompt: str,
    name: str,
    keywords: list,
    language: str,
    output: str,
    timeout: int,
    schedule: Optional[dict],
) -> dict:
    data: dict = {
        "name": name,
        "label": label,
        "category": category,
        "description": description,
        "enabled": False,
        "keywords": keywords,
        "language": language,
    }
    if schedule:
        data["schedule"] = schedule
    data["runtime"] = {"timeout_seconds": timeout, "skip_if_running": True}
    data["prompt"] = prompt if prompt.endswith("\n") else prompt + "\n"
    data["output"] = output
    return data


def _validate_template_payload(payload: dict, partial: bool, existing: Optional[str] = None) -> dict:
    """Validate template fields; merge with existing file when partial=True."""
    base: dict = {}
    if partial and existing:
        base = yaml_io.parse_yaml(_read_text(existing))
    elif not partial:
        for field in ("key", "label", "prompt"):
            if not str(payload.get(field, "")).strip():
                raise HTTPException(status_code=422, detail=ApiError.make(f"missing_{field}", f"缺少必填字段: {field}"))

    key = str(payload.get("key") or base.get("_key") or "").strip()
    if not partial:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,48}", key):
            raise HTTPException(
                status_code=422,
                detail=ApiError.make(
                    "invalid_template_key",
                    "模板 key 只能使用小写字母、数字、下划线、连字符（2-49 字符），且不能以 _ 开头",
                ),
            )

    label = str(payload.get("label", base.get("label", ""))).strip()
    category = str(payload.get("category", base.get("category", ""))).strip()
    if category and category not in TEMPLATE_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=ApiError.make("invalid_category", f"分类必须是: {', '.join(sorted(TEMPLATE_CATEGORIES))}"),
        )
    description = str(payload.get("description", base.get("description", ""))).strip()
    if len(description) > 300:
        raise HTTPException(status_code=422, detail=ApiError.make("description_too_long", "描述不能超过 300 字符"))

    prompt = str(payload.get("prompt", base.get("prompt", "")))
    if not prompt.strip():
        raise HTTPException(status_code=422, detail=ApiError.make("missing_prompt", "缺少 prompt 内容"))
    if len(prompt.strip()) < 20:
        raise HTTPException(status_code=422, detail=ApiError.make("prompt_too_short", "prompt 内容过短（至少 20 字符）"))
    unknown = [v for v in _prompt_variables({"prompt": prompt}) if v not in PROMPT_VARIABLES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=ApiError.make(
                "unknown_variables",
                f"未知变量: {', '.join(unknown)}；可用变量: {', '.join(sorted(PROMPT_VARIABLES))}",
            ),
        )

    name = str(payload.get("name", base.get("name", ""))).strip() or f"<{label or key}>"
    language = str(payload.get("language", base.get("language", "zh"))).strip() or "zh"
    keywords = payload.get("keywords", base.get("keywords", []) or [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",") if k.strip()]
    if not isinstance(keywords, list):
        raise HTTPException(status_code=422, detail=ApiError.make("invalid_keywords", "keywords 必须是列表"))
    keywords = [str(k).strip() for k in keywords if str(k).strip()][:20]

    output = str(payload.get("output", base.get("output", ""))).strip()
    if not output:
        output = f"output/{category or 'custom'}/{{date}}_{{name}}.md"
    if output.startswith("/") or ".." in output or not output.endswith(".md"):
        raise HTTPException(
            status_code=422,
            detail=ApiError.make("invalid_output", "output 必须是相对路径、以 .md 结尾且不含 .."),
        )
    bad_out_vars = [v for v in re.findall(r"\{(\w+)\}", output) if v not in OUTPUT_VARIABLES]
    if bad_out_vars:
        raise HTTPException(
            status_code=422,
            detail=ApiError.make("invalid_output", f"output 中未知变量: {', '.join(bad_out_vars)}"),
        )

    timeout = payload.get("timeout_seconds", base.get("runtime", {}).get("timeout_seconds", 3600))
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=ApiError.make("invalid_timeout", "timeout_seconds 必须是整数"))
    if not 60 <= timeout <= 86400:
        raise HTTPException(status_code=422, detail=ApiError.make("invalid_timeout", "timeout_seconds 需在 60-86400 之间"))

    schedule = payload.get("schedule", base.get("schedule"))
    if schedule is not None:
        if not isinstance(schedule, dict):
            raise HTTPException(status_code=422, detail=ApiError.make("invalid_schedule", "schedule 必须是对象"))
        stype = str(schedule.get("type", "manual")).strip()
        if stype not in SCHEDULE_TYPES:
            raise HTTPException(
                status_code=422,
                detail=ApiError.make("invalid_schedule", f"schedule.type 必须是: {', '.join(sorted(SCHEDULE_TYPES))}"),
            )
        stime = str(schedule.get("time", "08:00")).strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", stime):
            raise HTTPException(status_code=422, detail=ApiError.make("invalid_schedule", "schedule.time 格式必须是 HH:MM"))
        schedule = {"type": stype, "time": stime, "timezone": str(schedule.get("timezone", "Asia/Shanghai")).strip()}

    data = _template_data(key, label, category, description, prompt, name, keywords, language, output, timeout, schedule)
    data["_key"] = key
    return data


def _write_template(tmpl_dir: str, data: dict) -> str:
    key = data.pop("_key")
    file_path = _template_path(tmpl_dir, key)
    yaml_io.atomic_write(file_path, yaml_io.dump_yaml(data))
    return file_path


def _template_fields(parsed: dict) -> dict:
    """Parsed template fields exposed to the UI (avoids client-side YAML parsing)."""
    runtime = parsed.get("runtime") or {}
    return {
        "job_name": str(parsed.get("name") or ""),
        "prompt": str(parsed.get("prompt") or ""),
        "keywords": list(parsed.get("keywords", []) or []),
        "language": str(parsed.get("language") or "zh"),
        "timeout_seconds": runtime.get("timeout_seconds"),
        "schedule": parsed.get("schedule") or None,
    }


def _read_template(tmpl_dir: str, key: str) -> dict:
    file_path = _template_path(tmpl_dir, key)
    content = _read_text(file_path)
    parsed = yaml_io.parse_yaml(content)
    return {
        "key": key,
        "name": key.lstrip("_"),
        "filename": os.path.basename(file_path),
        "label": str(parsed.get("label") or parsed.get("name") or key),
        "category": str(parsed.get("category") or ""),
        "description": str(parsed.get("description") or ""),
        "builtin": key.startswith("_"),
        "variables": _prompt_variables(parsed),
        "output_template": str(parsed.get("output") or ""),
        "updated_at": _file_mtime(file_path),
        **_template_fields(parsed),
        "content": content,
    }


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

    # Sanitize: prevent path traversal in name and category (keep CJK letters)
    safe_name = re.sub(r"[^\w\-,]", "_", name.replace(" ", "_").lower())
    safe_name = re.sub(r"_+", "_", safe_name).strip("_") or "job"
    if category:
        safe_category = re.sub(r"[^\w\-/]", "", category.strip().lower())
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

    # If template provided, use it as base and apply form overrides
    if template_name:
        tmpl_dir = os.path.join(settings.paths.jobs_dir, "_templates")
        base_content = _load_template_content(tmpl_dir, template_name)
        if base_content:
            base_data = yaml_io.parse_yaml(base_content)
            if name:
                base_data["name"] = name
            if description:
                base_data["description"] = description
            if keywords:
                base_data["keywords"] = keywords
            if language:
                base_data["language"] = language
            if prompt:
                base_data["prompt"] = prompt
            if output:
                base_data["output"] = output
            base_data["enabled"] = True
            base_data.pop("label", None)
            base_data.pop("category", None)
            base_content = yaml_io.dump_yaml(base_data)
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


def _load_template_content(tmpl_dir: str, template_name: str) -> str:
    """Resolve a template reference (filename, key, or key without underscore) to file content."""
    if not template_name or os.path.sep in template_name or template_name.startswith("."):
        return ""
    candidates = [template_name]
    if not template_name.endswith((".yaml", ".yml")):
        candidates += [f"{template_name}.yaml", f"_{template_name}", f"_{template_name}.yaml"]
    for cand in candidates:
        path = os.path.join(tmpl_dir, cand)
        if os.path.isfile(path):
            try:
                return _read_text(path)
            except OSError:
                return ""
    return ""


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

    # Budget guard applies to ad-hoc runs too, not just matrix rounds
    try:
        from core.budget import month_spend, run_allowed
        from core.config import load_system_config

        allowed, reason = run_allowed(
            month_spend(load_system_config(settings.paths.config_dir)), "该 Job")
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=ApiError.make("budget_exceeded", reason),
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - never block a run on config issues
        logger.warning("Budget check skipped: %s", exc)

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


@router.get("/{name:path}/logs")
def get_job_logs(name: str, request: Request,
                 run_id: Optional[str] = None,
                 limit: int = 2000,
                 user=Depends(require_viewer)):
    """Persisted run logs for a job (survives refresh and server restarts)."""
    settings = get_settings()
    if not load_job(settings.paths.jobs_dir, name):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", f"Job 不存在: {name}"),
        )
    try:
        from web.runner.log_store import read_events

        runs, events = read_events(
            settings.paths.logs_dir, name, run_id=run_id,
            limit=max(1, min(int(limit), 20000)),
        )
    except Exception as exc:  # noqa: BLE001 - never 500 on log reads
        logger.warning("Failed to read persisted logs for %s: %s", name, exc)
        return {"runs": [], "events": [], "run": None}

    current = _registry.get(name)
    live = bool(current and current.is_running)
    return {
        "runs": runs,
        "events": events,
        "run": runs[0] if runs else None,
        "live": live,
    }


@router.get("/{name:path}/stream")
async def stream_job(name: str, request: Request, user=Depends(require_viewer)):
    """SSE endpoint: streams log + status events for a job.

    Supports Last-Event-ID for reconnection.
    """
    task = _registry.get(name)
    if not task:
        # An existing job with no run in this process is "idle", not an error:
        # a 404 here makes EventSource retry in a loop and leaves the UI stuck
        # on "waiting for logs" after the user starts a run.
        settings = get_settings()
        if not load_job(settings.paths.jobs_dir, name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=ApiError.make("no_task", f"没有找到运行记录: {name}"),
            )

        async def idle_event():
            yield _sse_format({"type": "status", "status": "idle"})

        return StreamingResponse(
            idle_event(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
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


@router.post("/{name:path}/validate", response_model=dict)
def validate_yaml(
    name: str,
    payload: JobYamlUpdate,
    user=Depends(require_editor),
):
    """Check a job's YAML without saving it.

    The editor needs to know that a document is broken *before* it overwrites
    the file that a production run depends on. Same validators as the save
    path, so a green result here means the save will not be rejected.
    """
    settings = get_settings()
    if not load_job(settings.paths.jobs_dir, name):
        # Same contract as the save path: a name that does not resolve must not
        # report "valid" and then fail on save.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("job_not_found", f"未找到 job: {name}"),
        )
    errors: list[str] = []
    warnings: list[str] = []

    try:
        parsed = yaml_io.parse_yaml(payload.yaml_content)
    except ValueError as exc:
        parsed = None
        errors.append(str(exc))
    if not parsed:
        if not errors:
            errors.append("YAML 为空或不是映射结构")
        return {"ok": False, "errors": errors, "warnings": warnings}

    errors.extend(yaml_io.validate_output_path(
        parsed, output_root=os.path.join(settings.paths.output_dir)))
    try:
        warnings = yaml_io.validate_yaml(parsed)
    except Exception as exc:  # noqa: BLE001 - a linter crash is not a save blocker
        warnings = [f"校验器异常，已跳过风格检查: {type(exc).__name__}"]

    # A job must still be runnable by the engine, not merely parseable.
    if not str(parsed.get("prompt") or "").strip():
        errors.append("prompt 为空：该 job 无法生成报告")
    if not str(parsed.get("name") or "").strip():
        warnings.append("缺少 name 字段，将回退到文件名")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


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

    parsed = None
    try:
        parsed = yaml_io.parse_yaml(payload.yaml_content)
    except ValueError:
        parsed = None  # reported by save_job_yaml below
    if parsed:
        output_errors = yaml_io.validate_output_path(
            parsed, output_root=os.path.join(settings.paths.output_dir))
        if output_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_output", "；".join(output_errors)),
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
        prompt=str(job.get("prompt") or ""),
        language=str(job.get("language") or "zh"),
        timeout_seconds=(job.get("runtime") or {}).get("timeout_seconds"),
        schedule=job.get("schedule") or None,
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
            deleted_path = candidate
            break

    if not deleted_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("file_missing", "Job 文件不存在"),
        )

    # Back up before removing. A delete is the one edit here with no undo, and
    # if the file was never pushed, git is not a safety net either.
    backup_path = None
    try:
        with open(deleted_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Absolute: the caller (UI, CLI) need not share the server's cwd.
        backup_path = os.path.abspath(yaml_io.save_backup(deleted_path, content))
    except (OSError, ValueError) as exc:
        # A failed backup must not silently become a failed audit trail.
        logger.warning("Could not back up %s before delete: %s", rel, exc)

    try:
        os.remove(deleted_path)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ApiError.make("delete_failed", f"删除失败: {exc}"),
        )

    audit.log("job_delete", user=user["username"], target=rel, result="success",
              details={"file": os.path.relpath(deleted_path, jobs_dir),
                       "backup_path": backup_path or "",
                       "backed_up": bool(backup_path)},
              ip=request.client.host if request.client else None)
    return {
        "ok": True,
        "deleted": os.path.relpath(deleted_path, jobs_dir),
        "backup_path": backup_path,
    }
