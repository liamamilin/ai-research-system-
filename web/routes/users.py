"""User management routes (admin only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from web import audit
from web.auth import db as user_db
from web.auth.password import hash_password
from web.deps import require_admin
from web.models import ApiError, UserOut


def _user_out(row: dict) -> UserOut:
    return UserOut(
        id=row["id"],
        username=row["username"],
        role=row["role"],
        created_at=row.get("created_at"),
        last_login_at=row.get("last_login_at"),
        disabled=bool(row.get("disabled")),
    )

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("", response_model=list[UserOut])
def list_users(user=Depends(require_admin)):
    """List all users."""
    return [_user_out(r) for r in user_db.list_users()]


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: dict, request: Request, user=Depends(require_admin)):
    """Create a new user."""
    username = payload.get("username", "").strip()
    password = payload.get("password", "")
    role = payload.get("role", "viewer")

    if not username or len(username) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_username", "用户名至少 2 个字符"),
        )
    if not password or len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_password", "密码至少 6 个字符"),
        )
    if role not in ("admin", "editor", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_role", "角色必须为 admin/editor/viewer"),
        )

    existing = user_db.get_user_by_username(username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("user_exists", f"用户 '{username}' 已存在"),
        )

    pwd_hash = hash_password(password)
    uid = user_db.create_user(username, pwd_hash, role)
    created = user_db.get_user_by_id(uid)

    audit.log(
        "user_create",
        user=user["username"],
        target=username,
        result="success",
        ip=request.client.host if request.client else None,
    )

    return _user_out(created)


@router.delete("/{user_id:int}")
def delete_user(user_id: int, request: Request, user=Depends(require_admin)):
    """Delete a user. Cannot delete yourself."""
    if user["id"] == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ApiError.make("cannot_self_delete", "不能删除自己"),
        )

    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("user_not_found", "用户不存在"),
        )

    user_db.revoke_user_sessions(user_id)
    user_db.delete_user(user_id)

    audit.log(
        "user_delete",
        user=user["username"],
        target=target["username"],
        result="success",
        ip=request.client.host if request.client else None,
    )

    return {"ok": True}


@router.patch("/{user_id:int}", response_model=UserOut)
def update_user(user_id: int, payload: dict, request: Request,
                user=Depends(require_admin)):
    """Update a user: role, disabled flag, password reset, session revoke.

    Admins cannot demote, disable, or delete themselves (guards against
    locking everyone out).
    """
    from web.auth.password import hash_password as _hash

    target = user_db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", f"用户不存在: {user_id}"),
        )

    is_self = user["id"] == user_id
    changes: list[str] = []

    role = payload.get("role")
    if role is not None:
        if role not in ("admin", "editor", "viewer"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_role", "角色必须为 admin/editor/viewer"),
            )
        if is_self and role != target["role"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ApiError.make("cannot_self_demote", "不能修改自己的角色"),
            )
        if role != target["role"]:
            user_db.set_role(user_id, role)
            changes.append(f"role={role}")

    disabled = payload.get("disabled")
    if disabled is not None:
        if is_self and disabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=ApiError.make("cannot_self_disable", "不能禁用自己"),
            )
        if bool(disabled) != bool(target.get("disabled")):
            user_db.set_disabled(user_id, bool(disabled))
            changes.append(f"disabled={bool(disabled)}")
            if disabled:
                user_db.revoke_user_sessions(user_id)

    new_password = payload.get("password")
    if new_password is not None:
        if len(new_password) < 6:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_password", "密码至少 6 个字符"),
            )
        user_db.update_password(user_id, _hash(new_password))
        user_db.revoke_user_sessions(user_id)
        changes.append("password_reset")

    if payload.get("revoke_sessions"):
        user_db.revoke_user_sessions(user_id)
        changes.append("sessions_revoked")

    audit.log(
        "user_update",
        user=user["username"],
        target=target["username"],
        result="success",
        details={"changes": changes},
        ip=request.client.host if request.client else None,
    )
    updated = user_db.get_user_by_id(user_id) or target
    return _user_out(updated)
