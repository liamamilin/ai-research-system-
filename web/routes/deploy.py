"""Deployment info routes: environment status, shutdown."""

from __future__ import annotations

import os
import signal
import subprocess
import threading

from fastapi import APIRouter, Depends, HTTPException

from web import audit
from web.deps import require_admin, require_viewer
from web.models import ApiError
from web.settings import detect_environment, get_settings

router = APIRouter(prefix="/api/deploy", tags=["deploy"])

# Same absolute location run_web.py writes, so shutdown finds it regardless of
# the server process's working directory.
PID_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "state", "server.pid")


@router.get("/status")
def deploy_status(user=Depends(require_viewer)):
    env = detect_environment()
    settings = get_settings()
    git_commit = ""
    git_branch = ""
    try:
        git_commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        git_branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        pass
    return {
        "environment": env,
        "host": settings.server.host,
        "port": settings.server.port,
        "git_commit": git_commit,
        "git_branch": git_branch,
        "cookie_secure": settings.auth.cookie_secure,
        "has_secret_key": settings.auth.secret_key not in ("CHANGE_ME", "dev-only-change-me-in-production-aaaaaaaaaaaaaaaaaa"),
        "rate_limits": {
            "login_per_minute": settings.rate_limit.login_per_minute,
            "job_run_per_minute": settings.rate_limit.job_run_per_minute,
        },
    }


@router.post("/shutdown")
def deploy_shutdown(user=Depends(require_admin)):
    """Gracefully shut down the server. Admin only."""
    if not os.path.isfile(PID_FILE):
        raise HTTPException(
            status_code=404,
            detail=ApiError.make("no_pid_file", "PID 文件不存在"),
        )

    with open(PID_FILE) as f:
        pid = int(f.read().strip())

    audit.log("server_shutdown", user=user["username"], target=f"pid:{pid}", result="success")

    def _kill():
        import time as _time
        _time.sleep(0.5)
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    threading.Thread(target=_kill, daemon=True).start()

    return {"ok": True, "message": f"正在停止服务器 (PID {pid})..."}
