"""System config routes: read/write system.yaml (admin only, secrets masked)."""

from __future__ import annotations

import os
import time
from io import StringIO
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.config import load_system_config
from core.llm import LLMClient, LLMConfig
from core.search import SearchClient, SearchConfig
from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services.yaml_io import parse_yaml, atomic_write, save_backup

router = APIRouter(prefix="/api/config", tags=["config"])

_ENV_PATH_OVERRIDE: Optional[str] = None


def _env_path() -> str:
    if _ENV_PATH_OVERRIDE:
        return _ENV_PATH_OVERRIDE
    return str(Path(__file__).resolve().parents[2] / ".env")


def set_env_path(path: Optional[str]) -> None:
    global _ENV_PATH_OVERRIDE
    _ENV_PATH_OVERRIDE = path


def _load_system() -> dict:
    from web.settings import get_settings
    return load_system_config(get_settings().paths.config_dir)


def _merged_system(overrides: Optional[dict] = None) -> dict:
    """Load system.yaml and deep-merge unsaved form overrides."""
    sys_config = _load_system()
    if overrides:
        _apply_patch(sys_config, overrides)
    return sys_config


def _secret_env_map() -> dict[str, str]:
    """Env var name → human label, derived from the current config."""
    sys_config = _load_system()
    ai = sys_config.get("ai") or {}
    search = sys_config.get("search") or {}
    extraction = sys_config.get("extraction") or {}

    mapping: dict[str, str] = {}
    if ai.get("api_key_env"):
        mapping[str(ai["api_key_env"])] = "AI 模型（LLM）"
    if ai.get("embedding_api_key_env"):
        mapping[str(ai["embedding_api_key_env"])] = "Embedding 模型"
    if search.get("api_key_env"):
        mapping.setdefault(str(search["api_key_env"]), "搜索服务")
    for provider, env_name in (search.get("api_key_envs") or {}).items():
        if env_name:
            mapping[str(env_name)] = f"搜索 · {provider}"
    if extraction.get("api_key_env"):
        mapping[str(extraction["api_key_env"])] = "报告清理（可选）"
    return mapping


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:4] + "****" + value[-2:]


def _write_env_value(env_name: str, value: Optional[str]) -> None:
    """Set or remove one key in the .env file (atomic, 0600)."""
    path = _env_path()
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key == env_name:
            replaced = True
            if value is not None:
                out.append(f'{env_name}="{value}"')
            continue
        out.append(line)

    if not replaced and value is not None:
        out.append(f'{env_name}="{value}"')

    content = "\n".join(out).rstrip() + "\n"
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)

    if value is not None:
        os.environ[env_name] = value
    else:
        os.environ.pop(env_name, None)


def _apply_patch(target: dict, patch: dict) -> None:
    """Deep-merge a patch into a ruamel document; None deletes a key."""
    from ruamel.yaml.comments import CommentedMap

    for key, value in patch.items():
        if value is None:
            if key in target:
                del target[key]
        elif isinstance(value, dict):
            node = target.get(key)
            if not isinstance(node, dict):
                node = CommentedMap()
                target[key] = node
            _apply_patch(node, value)
        else:
            target[key] = value


def _merge_patch(path: str, patch: dict) -> str:
    """Merge a field patch into system.yaml, preserving comments/formatting."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML()
    yaml.preserve_quotes = True
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f) or CommentedMap()
    else:
        data = CommentedMap()
    _apply_patch(data, patch)
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


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
        "parsed": parsed,
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

    patch = payload.get("patch")
    if patch is not None:
        if not isinstance(patch, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_patch", "patch 必须为对象"),
            )
        try:
            new_content = _merge_patch(path, patch)
        except Exception as e:  # noqa: BLE001 - reported to the admin
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("merge_failed", f"合并配置失败: {e}"),
            )
    else:
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


@router.get("/secrets")
def get_secrets(user=Depends(require_admin)):
    """List the API-key env vars referenced by the config (values masked)."""
    items = []
    for env_name, label in _secret_env_map().items():
        value = os.environ.get(env_name, "")
        items.append({
            "env": env_name,
            "label": label,
            "configured": bool(value),
            "masked": _mask(value),
        })
    return {"env_file": _env_path(), "secrets": items}


@router.put("/secrets")
def update_secrets(payload: dict, request: Request, user=Depends(require_admin)):
    """Set or clear API keys in .env. Keys are restricted to configured env names."""
    values = payload.get("values")
    if not isinstance(values, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_values", "values 必须为对象"),
        )

    allowed = _secret_env_map()
    applied: dict[str, bool] = {}
    for env_name, value in values.items():
        if env_name not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("unknown_secret", f"未在配置中引用的环境变量: {env_name}"),
            )
        if value is not None and not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_value", f"{env_name} 必须为字符串或 null"),
            )
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_value", "密钥不能包含换行"),
            )
        _write_env_value(env_name, value or None)
        applied[env_name] = value is not None

    audit.log(
        "config_secrets_update",
        user=user["username"],
        result="success",
        details={"keys": sorted(applied), "cleared": [k for k, v in applied.items() if not v]},
        ip=request.client.host if request.client else None,
    )

    items = []
    for env_name, label in allowed.items():
        value = os.environ.get(env_name, "")
        items.append({
            "env": env_name,
            "label": label,
            "configured": bool(value),
            "masked": _mask(value),
        })
    return {"ok": True, "secrets": items}


# ---------------------------------------------------------------------------
# Connectivity tests (each tests ONE thing, against unsaved form values)
# ---------------------------------------------------------------------------


@router.post("/test-llm")
def test_llm(payload: dict, user=Depends(require_admin)):
    """Send a tiny chat completion with the (possibly unsaved) LLM config."""
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)
    llm_cfg = LLMConfig.from_system(sys_config, "ai")
    if api_key:
        llm_cfg.api_key = api_key
    llm_cfg.max_tokens = 16
    llm_cfg.timeout = min(llm_cfg.timeout, 60)
    llm_cfg.request_timeout = 60
    llm_cfg.max_retries = 0

    started = time.time()
    try:
        client = LLMClient(llm_cfg)
        try:
            resp = client.chat(
                [{"role": "user", "content": "Reply with: ok"}],
                max_tokens=16,
                temperature=0.0,
            )
        finally:
            client.close()
        return {
            "ok": True,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "reply": (resp.content or "")[:80],
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        return {
            "ok": False,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


@router.post("/test-search")
def test_search(payload: dict, user=Depends(require_admin)):
    """Run one small search with the (possibly unsaved) search config."""
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)
    search_cfg = SearchConfig.from_system(sys_config)
    if api_key:
        search_cfg.api_key = api_key
        search_cfg.api_keys[search_cfg.provider] = api_key
    search_cfg.max_results = 1
    search_cfg.max_chars_per_result = 200
    search_cfg.timeout = min(search_cfg.timeout, 30)

    started = time.time()
    try:
        client = SearchClient(search_cfg)
        hits = client.search(["connectivity test"], objective="connectivity check")
        result = {
            "ok": bool(hits),
            "provider": search_cfg.provider,
            "results": len(hits),
            "latency_ms": int((time.time() - started) * 1000),
        }
        if hits:
            result["sample_url"] = hits[0].url
        if not hits:
            result["error"] = "无结果返回（检查服务商与密钥）"
        return result
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        return {
            "ok": False,
            "provider": search_cfg.provider,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


@router.post("/test-embedding")
def test_embedding(payload: dict, user=Depends(require_admin)):
    """Embed a probe string with the (possibly unsaved) embedding config."""
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)

    started = time.time()
    try:
        from core.embeddings import EmbeddingClient

        client = EmbeddingClient.from_system(sys_config)
        if api_key:
            client.api_key = api_key
        vector = client.embed(["ping"])[0]
        return {
            "ok": True,
            "model": client.model,
            "base_url": client.base_url,
            "dims": len(vector),
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        return {
            "ok": False,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


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
