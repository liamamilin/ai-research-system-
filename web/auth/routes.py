"""Authentication routes: /login, /logout, /refresh, /me."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response, status

from web import audit
from web.auth import db as user_db
from web.auth import jwt as jwt_helper
from web.auth.password import verify_password
from web.models import ApiError, LoginRequest, TokenResponse, UserOut
from web.settings import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

ACCESS_COOKIE = "ai_research_access"
REFRESH_COOKIE = "ai_research_refresh"
CSRF_COOKIE = "ai_research_csrf"


def _set_auth_cookies(response: Response, access: str, refresh: str, csrf: str):
    settings = get_settings()
    secure = settings.auth.cookie_secure
    samesite = settings.auth.cookie_samesite  # type: ignore[assignment]
    access_max = settings.auth.access_token_minutes * 60
    refresh_max = settings.auth.refresh_token_days * 86400

    response.set_cookie(
        ACCESS_COOKIE, access,
        max_age=access_max, httponly=True, secure=secure, samesite=samesite, path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE, refresh,
        max_age=refresh_max, httponly=True, secure=secure, samesite=samesite,
        path="/api/auth",  # only sent to refresh endpoint
    )
    # CSRF is NOT httpOnly: client JS reads it and echoes in X-CSRF-Token header
    response.set_cookie(
        CSRF_COOKIE, csrf,
        max_age=access_max, httponly=False, secure=secure, samesite=samesite, path="/",
    )


def _clear_auth_cookies(response: Response):
    for name, path in [(ACCESS_COOKIE, "/"), (REFRESH_COOKIE, "/api/auth"), (CSRF_COOKIE, "/")]:
        response.delete_cookie(name, path=path)


def _user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        created_at=row.get("created_at"),
        last_login_at=row.get("last_login_at"),
        disabled=bool(row.get("disabled")),
    )


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, response: Response):
    user = user_db.get_user_by_username(payload.username)
    ip = request.client.host if request.client else None

    if not user or user.get("disabled"):
        audit.log("login", user=payload.username, result="failed",
                  details={"reason": "no_user_or_disabled"}, ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_credentials", "用户名或密码错误"),
        )

    if not verify_password(payload.password, user["password_hash"]):
        audit.log("login", user=payload.username, result="failed",
                  details={"reason": "bad_password"}, ip=ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_credentials", "用户名或密码错误"),
        )

    access, access_jti, access_exp = jwt_helper.create_token(
        user["id"], user["username"], user["role"], "access"
    )
    refresh, refresh_jti, refresh_exp = jwt_helper.create_token(
        user["id"], user["username"], user["role"], "refresh"
    )
    csrf = jwt_helper.new_csrf_token()

    user_db.store_session(access_jti, user["id"], "access", access_exp)
    user_db.store_session(refresh_jti, user["id"], "refresh", refresh_exp)
    user_db.update_last_login(user["id"])

    # Reload user to get updated last_login_at
    user = user_db.get_user_by_id(user["id"]) or user

    _set_auth_cookies(response, access, refresh, csrf)
    audit.log("login", user=payload.username, result="success", ip=ip)

    return TokenResponse(user=_user_out(user), csrf_token=csrf)


@router.post("/logout")
def logout(request: Request, response: Response):
    access = request.cookies.get(ACCESS_COOKIE)
    refresh = request.cookies.get(REFRESH_COOKIE)

    # CSRF check: only block session revocation, not cookie clearing
    cookie_csrf = request.cookies.get(CSRF_COOKIE)
    header_csrf = request.headers.get("x-csrf-token")
    csrf_ok = bool(cookie_csrf and header_csrf and cookie_csrf == header_csrf)

    if csrf_ok:
        for tok in (access, refresh):
            if not tok:
                continue
            payload = jwt_helper.decode_token(tok)
            if payload and payload.get("jti"):
                user_db.revoke_session(payload["jti"])

    # Always clear cookies (CSRF can't prevent this)
    _clear_auth_cookies(response)

    if not csrf_ok and access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ApiError.make("csrf_failed", "CSRF 校验失败"),
        )

    audit.log("logout", ip=request.client.host if request.client else None)
    return {"ok": True}


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: Request, response: Response):
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("no_refresh_token", "缺少 refresh token"),
        )

    # CSRF check for refresh
    cookie_csrf = request.cookies.get(CSRF_COOKIE)
    header_csrf = request.headers.get("x-csrf-token")
    if not cookie_csrf or cookie_csrf != header_csrf:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ApiError.make("csrf_failed", "CSRF 校验失败"),
        )

    payload = jwt_helper.decode_token(token)
    if not payload or payload.get("kind") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_refresh", "refresh token 无效"),
        )

    jti = payload.get("jti")
    if jti and user_db.is_revoked(jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("revoked", "session 已被撤销"),
        )

    user = user_db.get_user_by_id(int(payload["sub"]))
    if not user or user.get("disabled"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("user_disabled", "用户被禁用"),
        )

    # Rotate access token; keep the refresh as-is (could rotate too)
    access, access_jti, access_exp = jwt_helper.create_token(
        user["id"], user["username"], user["role"], "access"
    )
    csrf = jwt_helper.new_csrf_token()
    user_db.store_session(access_jti, user["id"], "access", access_exp)
    _set_auth_cookies(response, access, token, csrf)

    return TokenResponse(user=_user_out(user), csrf_token=csrf)


@router.get("/me", response_model=UserOut)
def me(request: Request):
    # Lazy import to avoid circular dep
    from web.deps import current_user
    user = current_user(request)
    return _user_out(user)


# ---------------------------------------------------------------------------
# Self-service account management
# ---------------------------------------------------------------------------


@router.post("/change-password")
def change_password(payload: dict, request: Request, response: Response):
    """Change the current user's password.

    Requires the current password; keeps the current session alive and
    revokes all other sessions.
    """
    from web.auth.password import hash_password as _hash

    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if not payload_jwt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("not_authenticated", "未登录"),
        )
    user_id = int(payload_jwt["sub"])
    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "用户不存在"),
        )

    current = payload.get("current_password", "")
    new_password = payload.get("new_password", "")
    if not verify_password(current, target["password_hash"]):
        audit.log("change_password", user=target["username"], result="failed",
                  details={"reason": "bad_current_password"},
                  ip=request.client.host if request.client else None)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_credentials", "当前密码不正确"),
        )
    if len(new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_password", "新密码至少 6 个字符"),
        )

    user_db.update_password(user_id, _hash(new_password))

    # Keep the current access + refresh sessions, revoke everything else
    keep = [payload_jwt.get("jti")]
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    refresh_jwt = jwt_helper.decode_token(refresh_token) if refresh_token else None
    if refresh_jwt and refresh_jwt.get("jti"):
        keep.append(refresh_jwt["jti"])
    user_db.revoke_user_sessions(user_id, except_jtis=[j for j in keep if j])

    audit.log("change_password", user=target["username"], result="success",
              ip=request.client.host if request.client else None)
    return {"ok": True}


@router.get("/sessions")
def my_sessions(request: Request):
    """List the current user's active sessions."""
    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if not payload_jwt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("not_authenticated", "未登录"),
        )
    user_id = int(payload_jwt["sub"])
    current_jti = payload_jwt.get("jti")
    sessions = user_db.list_sessions(user_id)
    return {
        "sessions": [
            {
                "jti": s["jti"],
                "kind": s["kind"],
                "expires_at": s["expires_at"],
                "current": s["jti"] == current_jti,
            }
            for s in sessions
        ]
    }


@router.delete("/sessions/{jti}")
def revoke_session(jti: str, request: Request, response: Response):
    """Revoke one of the current user's sessions (e.g. another device)."""
    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if not payload_jwt:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("not_authenticated", "未登录"),
        )
    user_id = int(payload_jwt["sub"])
    session = user_db.get_session(jti)
    if not session or session["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "session 不存在"),
        )
    user_db.revoke_session(jti)
    audit.log("session_revoke", user=payload_jwt.get("username"),
              target=jti[:12], result="success",
              ip=request.client.host if request.client else None)

    current_jti = payload_jwt.get("jti")
    if jti == current_jti:
        _clear_auth_cookies(response)
    return {"ok": True}


# ---------------------------------------------------------------------------
# API tokens (programmatic access)
# ---------------------------------------------------------------------------


@router.get("/tokens")
def list_tokens(request: Request):
    """List the current user's API tokens (metadata only)."""
    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if payload_jwt:
        user_id = int(payload_jwt["sub"])
    else:
        header = request.headers.get("authorization", "")
        row = user_db.get_api_token(header[7:].strip()) if header.lower().startswith("bearer ") else None
        if not row:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                detail=ApiError.make("not_authenticated", "未登录"))
        user_id = int(row["user_id"])

    tokens = user_db.list_api_tokens(user_id)
    return {
        "tokens": [
            {
                "id": t["id"], "name": t["name"], "prefix": t["prefix"],
                "created_at": t["created_at"], "last_used_at": t["last_used_at"],
                "expires_at": t["expires_at"], "revoked": bool(t["revoked"]),
            }
            for t in tokens
        ]
    }


@router.post("/tokens")
def create_token(payload: dict, request: Request):
    """Create an API token. The plaintext value is returned only once."""
    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if not payload_jwt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=ApiError.make("not_authenticated", "登录后创建 token"))
    user_id = int(payload_jwt["sub"])
    name = (payload.get("name") or "").strip() or "api-token"

    expires_at = None
    days = payload.get("expires_days")
    if days:
        try:
            expires_at = int(time.time()) + int(days) * 86400
        except (TypeError, ValueError):
            expires_at = None

    token_id, plaintext = user_db.create_api_token(user_id, name[:60], expires_at)
    audit.log("api_token_create", user=payload_jwt.get("username"),
              target=f"token:{token_id}", result="success",
              ip=request.client.host if request.client else None)
    return {
        "id": token_id, "name": name[:60], "token": plaintext,
        "prefix": plaintext[:12], "expires_at": expires_at,
    }


@router.delete("/tokens/{token_id:int}")
def revoke_token(token_id: int, request: Request):
    """Revoke one of the current user's API tokens."""
    token = request.cookies.get(ACCESS_COOKIE)
    payload_jwt = jwt_helper.decode_token(token) if token else None
    if not payload_jwt:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail=ApiError.make("not_authenticated", "未登录"))
    user_id = int(payload_jwt["sub"])
    if not user_db.revoke_api_token(user_id, token_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=ApiError.make("not_found", "token 不存在"))
    audit.log("api_token_revoke", user=payload_jwt.get("username"),
              target=f"token:{token_id}", result="success",
              ip=request.client.host if request.client else None)
    return {"ok": True}
