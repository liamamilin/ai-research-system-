"""FastAPI dependency injection helpers: auth, roles, CSRF."""

from __future__ import annotations

from typing import Callable, Optional

from fastapi import Depends, HTTPException, Request, status

from web.auth import db as user_db
from web.auth import jwt as jwt_helper
from web.auth.routes import ACCESS_COOKIE, CSRF_COOKIE
from web.models import ApiError

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _user_from_api_token(request: Request) -> Optional[dict]:
    """Resolve a user from an ``Authorization: Bearer air_...`` API token."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    raw = header[7:].strip()
    if not raw.startswith("air_"):
        return None
    row = user_db.get_api_token(raw)
    if not row:
        return None
    user = user_db.get_user_by_id(int(row["user_id"]))
    if not user or user.get("disabled"):
        return None
    try:
        user_db.touch_api_token(int(row["id"]))
    except Exception:
        pass
    user["_auth"] = "token"
    return user


def current_user(request: Request) -> dict:
    """Resolve the current user from the access cookie or an API token."""
    token_user = _user_from_api_token(request)
    if token_user:
        return token_user

    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("not_authenticated", "未登录"),
        )
    payload = jwt_helper.decode_token(token)
    if not payload or payload.get("kind") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_token", "token 无效"),
        )
    jti = payload.get("jti")
    if jti and user_db.is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("revoked", "session 已撤销"),
        )
    user = user_db.get_user_by_id(int(payload["sub"]))
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("user_disabled", "用户被禁用"),
        )
    return user


def require_role(min_role: str) -> Callable:
    """Return a dependency that ensures the current user has at least `min_role`."""
    if min_role not in _ROLE_RANK:
        raise ValueError(f"Unknown role: {min_role}")

    def _dep(request: Request, user: dict = Depends(current_user)) -> dict:
        # CSRF check for unsafe methods (skipped for API-token auth: no cookies)
        if request.method not in _SAFE_METHODS and user.get("_auth") != "token":
            cookie_csrf = request.cookies.get(CSRF_COOKIE)
            header_csrf = request.headers.get("x-csrf-token")
            if not cookie_csrf or cookie_csrf != header_csrf:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=ApiError.make("csrf_failed", "CSRF 校验失败"),
                )

        if _ROLE_RANK[user["role"]] < _ROLE_RANK[min_role]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=ApiError.make(
                    "forbidden",
                    f"需要 {min_role} 及以上权限",
                    {"current_role": user["role"], "required": min_role},
                ),
            )
        return user

    return _dep


require_viewer = require_role("viewer")
require_editor = require_role("editor")
require_admin = require_role("admin")
