"""Public setup status for first-run guidance (no auth required)."""

from __future__ import annotations

import os

from fastapi import APIRouter

from core.config import load_system_config
from web.auth import db as user_db
from web.settings import get_settings

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("/status")
def setup_status():
    """Non-sensitive first-run hints for the login page."""
    try:
        users = user_db.list_users()
        needs_admin = len(users) == 0
    except Exception:  # noqa: BLE001 - fresh install may have no DB yet
        needs_admin = True

    sys_config = load_system_config(get_settings().paths.config_dir)
    ai = sys_config.get("ai") or {}
    key_env = ai.get("api_key_env") or ""
    llm_configured = bool(ai.get("model")) and (not key_env or bool(os.environ.get(key_env)))

    search = sys_config.get("search") or {}
    search_env = search.get("api_key_env") or ""
    search_configured = (not search_env) or bool(os.environ.get(search_env))

    return {
        "needs_admin": needs_admin,
        "llm_configured": llm_configured,
        "search_configured": search_configured,
    }
