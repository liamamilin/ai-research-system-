"""Public read-only share endpoints."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, status

from web import share as share_mod
from web.models import ApiError
from web.routes.reports import resolve_output_file

router = APIRouter(prefix="/api/share", tags=["share"])


@router.get("/{token}")
def read_shared(token: str):
    """Read a shared report without authentication (token-gated)."""
    payload = share_mod.verify_share_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=ApiError.make("invalid_share", "分享链接无效或已过期"),
        )

    path = payload.get("path") or ""
    full_path = resolve_output_file(path, allowed_exts=(".md", ".mdx"))
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ApiError.make("read_error", str(e)),
        )

    return {
        "path": path,
        "title": os.path.splitext(os.path.basename(path))[0],
        "content": content,
        "size": len(content),
        "expires_at": payload.get("exp"),
        "shared_by": payload.get("by") or "",
    }
