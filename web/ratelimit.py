"""Simple in-memory rate limiter using sliding window counters."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.requests import Request


class RateLimiter:
    """Sliding-window rate limiter per key."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        bucket = self._buckets[key]
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True

    def reset(self, key: str):
        self._buckets.pop(key, None)


_ERROR_BODY = json.dumps({
    "error": {
        "code": "rate_limited",
        "message": "请求过于频繁，请稍后再试",
        "details": {},
    },
}).encode("utf-8")


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces rate limits for login and job-run endpoints.

    Rate limits are read from config/web.yaml at first request (lazy).
    """

    def __init__(self, app):
        super().__init__(app)
        self._login_limiter: Optional[RateLimiter] = None
        self._job_run_limiter: Optional[RateLimiter] = None

    def _get_login_limiter(self) -> RateLimiter:
        if self._login_limiter is None:
            try:
                from web.settings import get_settings
                cfg = get_settings().rate_limit
                self._login_limiter = RateLimiter(
                    max_requests=cfg.login_per_minute, window_seconds=60)
            except Exception:
                self._login_limiter = RateLimiter(max_requests=5, window_seconds=60)
        return self._login_limiter

    def _get_job_run_limiter(self) -> RateLimiter:
        if self._job_run_limiter is None:
            try:
                from web.settings import get_settings
                cfg = get_settings().rate_limit
                self._job_run_limiter = RateLimiter(
                    max_requests=cfg.job_run_per_minute, window_seconds=60)
            except Exception:
                self._job_run_limiter = RateLimiter(max_requests=10, window_seconds=60)
        return self._job_run_limiter

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        method = request.method

        if method == "POST":
            if path == "/api/auth/login":
                limiter = self._get_login_limiter()
                key = f"login:{request.client.host if request.client else 'unknown'}"
                if not limiter.check(key):
                    return Response(
                        content=_ERROR_BODY, status_code=429,
                        media_type="application/json", headers={"Retry-After": "60"},
                    )

            elif "/api/jobs/" in path and path.endswith("/run"):
                limiter = self._get_job_run_limiter()
                key = f"job_run:{request.cookies.get('ai_research_access', '')[:16]}"
                if not limiter.check(key):
                    return Response(
                        content=_ERROR_BODY, status_code=429,
                        media_type="application/json", headers={"Retry-After": "60"},
                    )

        return await call_next(request)
