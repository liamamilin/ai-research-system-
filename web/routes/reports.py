"""Report browsing + search API routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, status

from core import report_meta
from web.deps import require_viewer
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


@router.get("")
def list_reports(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    category: str | None = None,
    job: str | None = None,
    user=Depends(require_viewer),
):
    data = index_db.list_reports(
        page=page,
        per_page=per_page,
        category=category,
        job_name=job,
    )
    meta_map = report_meta.load_all()
    for item in data.get("items", []):
        item["meta"] = meta_map.get(report_meta.normalize_rel(item.get("path", "")))
    return data


@router.get("/raw")
def raw_report(path: str, user=Depends(require_viewer)):
    """Read the raw markdown content of a report file.

    Path is relative to the output directory, sanitized to prevent traversal.
    """
    settings = get_settings()
    output_dir = os.path.abspath(settings.paths.output_dir)

    # Sanitize: normalize and check for traversal
    safe = os.path.normpath(path).lstrip("/")
    full_path = os.path.abspath(os.path.join(output_dir, safe))

    # Ensure the resolved path is within output_dir
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

    if not full_path.endswith((".md", ".mdx")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ApiError.make("invalid_extension", "只支持 .md / .mdx 文件"),
        )

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
