"""System config routes: read/write system.yaml (admin only, secrets masked)."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services.yaml_io import parse_yaml, atomic_write, save_backup

router = APIRouter(prefix="/api/config", tags=["config"])


def _mask_secrets(data: dict, parent_key: str = "") -> dict:
    """Recursively mask secret values in a config dict."""
    masked: dict = {}
    for key, value in data.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        # Check if this key or any parent key contains secret field names
        is_secret = any(s in full_key.lower() for s in ["secret", "api_key", "password", "token"])
        if is_secret and isinstance(value, str) and len(value) > 4:
            masked[key] = value[:4] + "****"
        elif isinstance(value, dict):
            masked[key] = _mask_secrets(value, full_key)
        elif isinstance(value, list):
            masked[key] = [
                _mask_secrets(v, full_key) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            masked[key] = value
    return masked


@router.get("/system")
def get_system_config(user=Depends(require_admin)):
    """Read system.yaml with secrets masked."""
    from web.settings import get_settings
    settings = get_settings()
    path = os.path.join(settings.paths.config_dir, "system.yaml")

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "system.yaml 不存在"),
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    mtime = os.path.getmtime(path)

    parsed = parse_yaml(raw)
    masked = _mask_secrets(parsed)

    return {
        "content": raw,
        "masked": masked,
        "mtime": mtime,
        "path": path,
    }


@router.put("/system")
def update_system_config(
    payload: dict,
    request: Request,
    user=Depends(require_admin),
):
    """Write system.yaml. Only admin can do this."""
    from web.settings import get_settings
    settings = get_settings()
    path = os.path.join(settings.paths.config_dir, "system.yaml")

    new_content = payload.get("content", "")
    if not new_content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("empty_content", "内容不能为空"),
        )

    # Conflict detection: refuse to overwrite edits made elsewhere
    expected_mtime = payload.get("expected_mtime")
    if expected_mtime is not None and os.path.isfile(path):
        current_mtime = os.path.getmtime(path)
        try:
            if abs(current_mtime - float(expected_mtime)) > 0.001:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=ApiError.make(
                        "conflict",
                        "system.yaml 已被外部修改，请刷新后重试",
                        details={"current_mtime": current_mtime},
                    ),
                )
        except (TypeError, ValueError):
            pass

    # Validate YAML
    try:
        parse_yaml(new_content)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("yaml_invalid", str(e)),
        )

    # Read old for backup
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()
            if old_content != new_content:
                save_backup(path, old_content)
        except OSError:
            pass

    atomic_write(path, new_content)

    audit.log(
        "system_config_update",
        user=user["username"],
        result="success",
        ip=request.client.host if request.client else None,
    )
    return {"ok": True, "mtime": os.path.getmtime(path)}

    return {"ok": True}


@router.post("/test-connection")
def test_connection(user=Depends(require_admin)):
    """Probe the configured LLM endpoint and search provider.

    Makes one tiny LLM call and one small search request. Admin only.
    """
    from core.config import load_system_config
    from core.llm import LLMClient, LLMConfig
    from core.search import SearchClient, SearchConfig
    from web.settings import get_settings

    settings = get_settings()
    sys_cfg = load_system_config(settings.paths.config_dir)
    result: dict = {}

    # --- LLM ---
    started = time.time()
    llm_cfg = LLMConfig.from_system(sys_cfg, "ai")
    try:
        llm_cfg.max_tokens = 16
        llm_cfg.timeout = 60
        llm_cfg.request_timeout = 60
        llm_cfg.max_retries = 0
        client = LLMClient(llm_cfg)
        try:
            resp = client.chat(
                [{"role": "user", "content": "Reply with: ok"}],
                max_tokens=16,
                temperature=0.0,
            )
        finally:
            client.close()
        result["llm"] = {
            "ok": True,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "reply": (resp.content or "")[:60],
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        result["llm"] = {
            "ok": False,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }

    # --- Search ---
    started = time.time()
    search_cfg = SearchConfig.from_system(sys_cfg)
    search_cfg.max_results = 1
    search_cfg.max_chars_per_result = 200
    search_cfg.timeout = 30
    try:
        client = SearchClient(search_cfg)
        hits = client.search(
            ["connectivity test"], objective="connectivity check"
        )
        result["search"] = {
            "ok": bool(hits),
            "provider": search_cfg.provider,
            "results": len(hits),
            "latency_ms": int((time.time() - started) * 1000),
        }
        if hits:
            result["search"]["sample_url"] = hits[0].url
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        result["search"] = {
            "ok": False,
            "provider": search_cfg.provider,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }

    return result
