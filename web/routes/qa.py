"""Q&A routes: hybrid retrieval + LLM answer with citations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core.config import load_system_config
from core.qa import ask_with_fallback, format_citations
from web import audit
from web.deps import require_editor, require_viewer
from web.indexer import db as index_db
from web.indexer import vectors
from web.models import ApiError
from web.settings import get_settings

router = APIRouter(prefix="/api/qa", tags=["qa"])


def _embed_query(question: str, sys_config: dict) -> list[float]:
    """Embed a question; empty vector means FTS-only retrieval."""
    try:
        from core.embeddings import EmbeddingClient

        client = EmbeddingClient.from_system(sys_config)
        return client.embed([question])[0]
    except Exception as exc:  # noqa: BLE001 - semantic is optional
        import logging

        logging.getLogger(__name__).info("Semantic retrieval disabled: %s", exc)
        return []


def _llm_factory(sys_config: dict):
    from core.llm import LLMClient, LLMConfig

    def make():
        return LLMClient(LLMConfig.from_system(sys_config))

    return make


@router.post("")
def ask_question(payload: dict, request: Request, limit: int = Query(6, ge=1, le=20),
                 user=Depends(require_viewer)):
    """Answer a question across indexed reports, with citations."""
    question = (payload.get("question") or "").strip()
    if len(question) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_question", "question 至少 2 个字符"),
        )

    sys_config = load_system_config(get_settings().paths.config_dir)
    query_vector = _embed_query(question, sys_config)
    hits = vectors.hybrid_search(question, query_vector or None, limit=limit)

    if not hits:
        return {"answer": "没有检索到相关资料，请尝试换一个问法或先运行/重建索引。",
                "citations": [], "mode": "fts" if not query_vector else "hybrid"}

    result = ask_with_fallback(question, hits, _llm_factory(sys_config))
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=ApiError.make("llm_failed", "生成回答失败（LLM 调用异常）",
                                 {"citations": format_citations(hits)}),
        )

    audit.log(
        "qa_ask",
        user=user["username"],
        result="success",
        details={"question": question[:120], "hits": len(hits)},
        ip=request.client.host if request.client else None,
    )
    return {
        "answer": result["answer"],
        "citations": result["citations"],
        "mode": "hybrid" if query_vector else "fts",
        "usage": result.get("usage") or {},
    }


@router.post("/reindex")
def reindex_vectors(request: Request, limit: int = Query(200, ge=1, le=2000),
                    purge: bool = Query(False, description="drop all vectors first (full rebuild)"),
                    prune: bool = Query(True, description="drop vectors whose report no longer exists"),
                    user=Depends(require_editor)):
    """Embed reports that are missing/stale in the vector store."""
    sys_config = load_system_config(get_settings().paths.config_dir)
    try:
        from core.embeddings import EmbeddingClient

        client = EmbeddingClient.from_system(sys_config)
    except Exception as exc:  # noqa: BLE001 - report as a config problem
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("embeddings_not_configured", str(exc)),
        )

    settings = get_settings()
    import os

    from web.indexer import vector_sync

    purged = None
    if purge:
        purged = vector_sync.purge_all()
    pruned = vector_sync.prune_orphans(settings.paths.output_dir,
                                        settings.paths.state_dir) if prune else None

    reports = index_db.list_reports(page=1, per_page=limit)["items"]
    embedded = skipped = failed = 0
    for item in reports:
        rel = item["path"]
        full = os.path.join(settings.paths.output_dir, rel)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            if not vectors.needs_embedding(rel, content, client.model):
                skipped += 1
                continue
            vectors.index_report(rel, content, client.embed, model=client.model)
            embedded += 1
        except Exception as exc:  # noqa: BLE001 - keep indexing the rest
            failed += 1
            import logging

            logging.getLogger(__name__).warning("Embed %s failed: %s", rel, str(exc)[:150])

    audit.log(
        "qa_reindex",
        user=user["username"],
        result="success",
        details={"embedded": embedded, "skipped": skipped, "failed": failed},
        ip=request.client.host if request.client else None,
    )
    return {"embedded": embedded, "skipped": skipped, "failed": failed,
            "purged": purged,
            "pruned": pruned,
            "coverage": vector_sync.coverage(settings.paths.state_dir),
            "stats": vectors.index_stats()}
