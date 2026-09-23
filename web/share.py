"""Signed read-only share tokens for reports."""

from __future__ import annotations

import time
from typing import Optional

import jwt

from web.settings import get_settings

_ALG = "HS256"
SCOPE = "report-share"
MAX_TTL_HOURS = 24 * 90


def create_share_token(path: str, ttl_hours: int = 168,
                       created_by: str = "") -> tuple[str, int]:
    """Return (token, expires_at)."""
    settings = get_settings()
    ttl_hours = max(1, min(int(ttl_hours), MAX_TTL_HOURS))
    exp = int(time.time()) + ttl_hours * 3600
    payload = {
        "scope": SCOPE,
        "path": path,
        "by": created_by,
        "iat": int(time.time()),
        "exp": exp,
    }
    token = jwt.encode(payload, settings.auth.secret_key, algorithm=_ALG)
    return token, exp


def verify_share_token(token: str) -> Optional[dict]:
    """Return the payload of a valid share token, else None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.auth.secret_key, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != SCOPE:
        return None
    return payload
