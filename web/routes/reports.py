"""Report browsing + search API routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core import report_meta
from web import audit
from web.deps import require_editor, require_viewer
from web.indexer import db as index_db
from web.models import ApiError
from web.settings import get_settings

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/stats")
def stats(user=Depends(require_viewer)):
    return index_db.get_stats()


@router.get("/categories")
def categories(user=Depends(require_viewer)):
    return index_db.list_categories()


@router.get("/tree")
def tree(user=Depends(require_viewer)):
    return index_db.get_report_tree()


@router.get("/tags")
def list_tags(user=Depends(require_viewer)):
    return {"tags": index_db.list_tags()}


@router.get("/meta")
def get_meta(path: str, user=Depends(require_viewer)):
    row = index_db.get_report(path)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "报告不存在"),
        )
    return row


@router.patch("/meta")
def update_meta(payload: dict, request: Request, user=Depends(require_editor)):
    """Update favorite / tags / read state of one report."""
    path = (payload.get("path") or "").strip()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_path", "缺少 path"),
        )

    favorite = payload.get("favorite")
    read = payload.get("read")
    tags = payload.get("tags")
    if favorite is not None and not isinstance(favorite, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_favorite", "favorite 必须为布尔值"),
        )
    if read is not None and not isinstance(read, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_read", "read 必须为布尔值"),
        )
    if tags is not None:
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_tags", "tags 必须为字符串数组"),
            )
        tags = [t.strip()[:40] for t in tags if t.strip()][:20]

    rating = payload.get("rating")
    rating_note = payload.get("rating_note")
    if rating is not None:
        if not isinstance(rating, int) or isinstance(rating, bool) or not 0 <= rating <= 5:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_rating", "rating 必须为 0-5 的整数"),
            )
    if rating_note is not None and not isinstance(rating_note, str):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_rating_note", "rating_note 必须为字符串"),
        )

    updated = index_db.set_report_meta(path, favorite=favorite, tags=tags,
                                       read=read, rating=rating,
                                       rating_note=rating_note)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "报告不存在"),
        )
    audit.log(
        "report_meta",
        user=user["username"],
        target=path,
        result="success",
        details={"favorite": favorite, "read": read, "tags": tags},
        ip=request.client.host if request.client else None,
    )
    return updated


@router.get("")
def list_reports(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
    job: str | None = None,
    favorite: bool | None = None,
    tag: str | None = None,
    unread: bool | None = None,
    user=Depends(require_viewer),
):
    data = index_db.list_reports(
        page=page,
        per_page=per_page,
        category=category,
        job_name=job,
        favorite=favorite,
        tag=tag,
        unread=unread,
    )
    meta_map = report_meta.load_all()
    for item in data.get("items", []):
        item["meta"] = meta_map.get(report_meta.normalize_rel(item.get("path", "")))
    return data


def resolve_output_file(path: str,
                        allowed_exts: tuple[str, ...] = (".md", ".mdx", ".json")) -> str:
    """Resolve a report path inside output/, rejecting traversal and bad types."""
    settings = get_settings()
    output_dir = os.path.abspath(settings.paths.output_dir)

    safe = os.path.normpath(path).lstrip("/")
    full_path = os.path.abspath(os.path.join(output_dir, safe))

    if not full_path.startswith(output_dir + os.sep) and full_path != output_dir:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ApiError.make("invalid_path", "路径不允许"),
        )

    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "文件不存在"),
        )

    if not full_path.endswith(allowed_exts):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ApiError.make("invalid_extension", "文件类型不支持"),
        )
    return full_path


@router.post("/share")
def create_share(payload: dict, request: Request, user=Depends(require_editor)):
    """Create a read-only share link for one report."""
    from web import share as share_mod

    path = (payload.get("path") or "").strip()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_path", "缺少 path"),
        )
    try:
        ttl_hours = int(payload.get("ttl_hours", 168))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_ttl", "ttl_hours 必须为整数"),
        )

    safe = os.path.normpath(path).lstrip("/")
    resolve_output_file(safe, allowed_exts=(".md", ".mdx"))

    token, expires_at = share_mod.create_share_token(
        safe, ttl_hours=ttl_hours, created_by=user["username"])
    audit.log(
        "report_share",
        user=user["username"],
        target=safe,
        result="success",
        details={"expires_at": expires_at},
        ip=request.client.host if request.client else None,
    )
    return {"token": token, "path": safe, "expires_at": expires_at,
            "url": f"/share/{token}"}


@router.get("/html")
def report_html(path: str, download: bool = False, user=Depends(require_viewer)):
    """Standalone HTML export of a report (printable to PDF)."""
    from fastapi.responses import HTMLResponse

    from core.render import render_report_html

    safe = os.path.normpath(path).lstrip("/")
    full_path = resolve_output_file(safe, allowed_exts=(".md", ".mdx"))
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    title = os.path.splitext(os.path.basename(safe))[0]
    document = render_report_html(title, content)
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{title}.html"'
    return HTMLResponse(document, headers=headers)


@router.post("/email")
def email_report(payload: dict, request: Request, user=Depends(require_editor)):
    """Email one report using the configured SMTP channel."""
    from core.config import load_system_config
    from core.notify import send_report

    path = (payload.get("path") or "").strip()
    if not path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("missing_path", "缺少 path"),
        )
    safe = os.path.normpath(path).lstrip("/")
    full_path = resolve_output_file(safe, allowed_exts=(".md", ".mdx"))
    with open(full_path, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    to_addrs = payload.get("to")
    if to_addrs is not None and (not isinstance(to_addrs, list)
                                 or any(not isinstance(t, str) for t in to_addrs)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_to", "to 必须为字符串数组"),
        )

    sys_config = load_system_config(get_settings().paths.config_dir)
    title = os.path.splitext(os.path.basename(safe))[0]
    try:
        delivered = send_report(title, content, to_addrs=to_addrs,
                                sys_config=sys_config)
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("email_not_configured", str(e)),
        )

    audit.log(
        "report_email",
        user=user["username"],
        target=safe,
        result="success" if delivered else "failed",
        ip=request.client.host if request.client else None,
    )
    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ApiError.make("email_failed", "邮件发送失败"),
        )
    return {"ok": True}


@router.get("/raw")
def raw_report(path: str, user=Depends(require_viewer)):
    """Read the raw markdown content of a report file.

    Path is relative to the output directory, sanitized to prevent traversal.
    """
    safe = os.path.normpath(path).lstrip("/")
    full_path = resolve_output_file(safe)

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=ApiError.make("read_error", str(e)),
        )

    return {
        "path": safe,
        "content": content,
        "size": len(content),
        "meta": report_meta.get(safe),
        "index": index_db.get_report(safe),
    }


@router.get("/search")
def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    user=Depends(require_viewer),
):
    """Full-text search across report titles and content using FTS5."""
    if not q.strip():
        return {"results": [], "total": 0}

    results = index_db.search_reports(q.strip(), limit=limit)
    meta_map = report_meta.load_all()
    for item in results:
        item["meta"] = meta_map.get(report_meta.normalize_rel(item.get("path", "")))
    return {"results": results, "total": len(results)}
