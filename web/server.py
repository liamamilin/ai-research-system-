"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from web.auth import db as user_db
from web.auth.routes import router as auth_router
from web.deps import require_admin
from web.models import ApiError
from web.ratelimit import RateLimitMiddleware
from web.routes.jobs import router as jobs_router
from web.routes.reports import router as reports_router
from web.routes.config import router as config_router
from web.routes.users import router as users_router
from web.routes.logs import router as logs_router
from web.routes.scheduler import router as scheduler_router
from web.routes.deploy import router as deploy_router
from web.routes.usage import router as usage_router
from web.routes.pipeline import router as pipeline_router
from web.routes.tracking import router as tracking_router
from web.routes.share import router as share_router
from web.routes.qa import router as qa_router
from web.routes.setup import router as setup_router
from web.runner.registry import TaskRegistry
from web.settings import get_settings
from web.indexer import db as index_db
from web.indexer.scanner import full_scan
from web.indexer.watcher import start_watcher

logger = logging.getLogger("ai_research.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = get_settings()
    from utils.logger import attach_file_logging
    from web.runner import pipeline as pipeline_runner

    log_cfg = {}
    try:
        from core.config import load_system_config
        log_cfg = (load_system_config(settings.paths.config_dir).get("logging") or {})
    except Exception:
        pass
    attach_file_logging(
        log_cfg.get("file") or os.path.join(settings.paths.logs_dir, "ai_research.log"),
        level=log_cfg.get("level", "INFO"),
    )

    loaded = pipeline_runner.load_persisted_rounds()
    if loaded:
        logger.info("Loaded %d persisted pipeline round(s)", loaded)

    user_db.init_db()

    # Initialize report indexer
    init_result = index_db.init_db()
    if init_result.get("fts_rebuilt"):
        # Tokenizer migration: the FTS table was recreated, so the documents
        # must be re-read before search works again.
        logger.warning("Full-text index rebuilt; rescanning reports...")
    scan_result = full_scan(settings.paths.output_dir)
    if scan_result["indexed"] > 0:
        logger.info("Indexed %d reports from %s", scan_result["indexed"], settings.paths.output_dir)
    elif scan_result["indexed"] == 0:
        logger.info("No reports found in %s", settings.paths.output_dir)
    # Start incremental file watcher
    start_watcher(settings.paths.output_dir)

    logger.info("Web UI started")
    yield
    # Shutdown: cancel all running tasks
    registry = TaskRegistry()
    running = registry.all_running()
    for job_name in running:
        logger.info("Cancelling running job on shutdown: %s", job_name)
        registry.cancel(job_name)
    logger.info("Web UI shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AI Research Console", version="0.1.0", lifespan=lifespan)

    # CORS for dev mode
    if settings.cors.enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors.origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Rate limiting
    app.add_middleware(RateLimitMiddleware)

    # ----- Unified error format -----

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiError.make("http_error", str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=ApiError.make("validation_error", "请求参数无效", {"errors": exc.errors()}),
        )

    # ----- Routers -----
    app.include_router(auth_router)
    app.include_router(jobs_router)
    app.include_router(reports_router)
    app.include_router(config_router)
    app.include_router(users_router)
    app.include_router(logs_router)
    app.include_router(scheduler_router)
    app.include_router(deploy_router)
    app.include_router(usage_router)
    app.include_router(pipeline_router)
    app.include_router(tracking_router)
    app.include_router(share_router)
    app.include_router(qa_router)
    app.include_router(setup_router)

    @app.get("/api/health")
    def health():
        """Liveness plus real dependency checks; 503 when something is broken."""
        from core.health import run_checks

        settings = get_settings()
        result = run_checks(
            state_dir=settings.paths.state_dir,
            output_dir=settings.paths.output_dir,
            config_dir=settings.paths.config_dir,
            deep=False,
        )
        return JSONResponse(
            status_code=503 if result["status"] == "error" else 200,
            content=result,
        )

    @app.get("/api/health/detailed")
    def health_detailed(user=Depends(require_admin)):
        """Full health report including index, vector and last-round state."""
        from core.health import run_checks, stale_locks

        settings = get_settings()
        result = run_checks(
            state_dir=settings.paths.state_dir,
            output_dir=settings.paths.output_dir,
            config_dir=settings.paths.config_dir,
            deep=True,
        )
        result["stale_locks"] = stale_locks(settings.paths.state_dir)
        return result

    # ----- Static SPA (production) -----
    ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
    if ui_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=ui_dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa_fallback(full_path: str):
            # Anything not handled by API routes returns index.html
            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content=ApiError.make("not_found", "API not found"),
                )
            index = ui_dist / "index.html"
            if index.is_file():
                return FileResponse(index)
            return JSONResponse(status_code=404, content=ApiError.make("not_found", "Not found"))
    else:

        @app.get("/")
        def root():
            return {
                "message": "AI Research Console API",
                "ui": "UI not built. Run: cd ui && npm run build",
                "docs": "/docs",
            }

    return app


app = create_app()
