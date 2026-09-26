"""In-memory sliding-window rate limiter.

The bucket keys have to be chosen carefully: an earlier version keyed the
job-run limit on the first 16 characters of the access cookie, but every HS256
JWT starts with the same base64 header (``eyJhbGciOiJIUzI1``), so the limit was
a single global bucket that one user could exhaust for everyone.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RateLimiter:
    """Sliding-window rate limiter per key.

    Buckets are swept on write, so a key that stops being used does not stay in
    the dict forever. Keying on attacker-influenced input (an IP) would
    otherwise be an unbounded memory growth path.
    """

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        # Amortised: at most once a window, so the cost is not per request.
        if now - self._last_sweep < self.window:
            return
        cutoff = now - self.window
        for key in [k for k, hits in self._buckets.items()
                    if not hits or hits[-1] < cutoff]:
            del self._buckets[key]
        self._last_sweep = now

    def check(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window
        self._sweep(now)
        bucket = self._buckets.setdefault(key, [])
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= self.max_requests:
            # Drop the key entirely once it is empty of anything actionable, so
            # a burst of one-off keys cannot grow the dict without bound.
            if not bucket:
                del self._buckets[key]
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


def _client_key(request: Request) -> str:
    """Identify the caller for per-client limits.

    ``request.client.host`` is the peer address. It is trustworthy because the
    server is started with ``proxy_headers=False``; otherwise a loopback client
    could set ``X-Forwarded-For`` and get a fresh bucket per request.
    """
    return request.client.host if request.client else "unknown"


def _principal_key(request: Request) -> str:
    """Identify the caller for per-account limits.

    Prefers the authenticated identity and falls back to a hash of whatever
    credential was presented. A raw prefix is useless: JWT headers are
    identical for every token, and API tokens have no cookie at all, so both
    paths used to collapse into one shared bucket.
    """
    cookie = request.cookies.get("ai_research_access", "")
    header = request.headers.get("authorization", "")
    token = cookie or (header[7:] if header.lower().startswith("bearer ") else "")
    if not token:
        return f"anon:{_client_key(request)}"
    # Hashed: the raw token must not be retained in memory as a dict key.
    return "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


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

    def _limited(self) -> Response:
        return Response(
            content=_ERROR_BODY, status_code=429,
            media_type="application/json", headers={"Retry-After": "60"},
        )

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path
        method = request.method

        if method == "POST":
            if path == "/api/auth/login":
                if not self._get_login_limiter().check(f"login:{_client_key(request)}"):
                    return self._limited()

            elif "/api/jobs/" in path and path.endswith("/run"):
                if not self._get_job_run_limiter().check(
                        f"job_run:{_principal_key(request)}"):
                    return self._limited()

        return await call_next(request)
