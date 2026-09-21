"""
Web search API clients.

Providers: Parallel (default), Tavily, Brave Search, Serper (Google), plus a
keyless DuckDuckGo fallback. All return ranked URLs with LLM-ready excerpts.

Configured via ``system.yaml``::

    search:
      provider: "parallel"          # parallel | tavily | brave | serper | duckduckgo
      api_key_env: "PARALLEL_API_KEY"
      api_key_envs:                 # per-provider keys (used when switching)
        tavily: "TAVILY_API_KEY"
        brave: "BRAVE_API_KEY"
        serper: "SERPER_API_KEY"
      mode: "advanced"              # turbo | fast | basic | advanced
      max_results: 10
      max_chars_per_result: 3000
      after_date: ""                # optional YYYY-MM-DD freshness bound
      fallback_provider: "duckduckgo"
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from .errors import CancelledError

logger = logging.getLogger(__name__)

_PARALLEL_ENDPOINT = "https://api.parallel.ai/v1/search"
_MAX_QUERIES = 5
_MAX_QUERY_CHARS = 200
_MAX_OBJECTIVE_CHARS = 5000

# Query size limits enforced with truncation
_ALLOWED_MODES = {"turbo", "fast", "basic", "advanced"}


@dataclass
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    publish_date: Optional[str] = None
    excerpts: list[str] = field(default_factory=list)
    provider: str = ""

    def to_markdown(self) -> str:
        date = f" — published {self.publish_date}" if self.publish_date else ""
        lines = [f"### [{self.title or self.url}]({self.url}){date}"]
        for excerpt in self.excerpts:
            if excerpt.strip():
                lines.append(excerpt.strip())
        return "\n\n".join(lines)


@dataclass
class SearchConfig:
    """Search provider configuration."""

    provider: str = "parallel"
    api_key: str = ""
    api_key_env: str = "PARALLEL_API_KEY"
    mode: str = "advanced"
    max_results: int = 10
    max_chars_per_result: int = 3000
    max_chars_total: int = 60000
    after_date: str = ""
    timeout: int = 60
    fallback_provider: str = "duckduckgo"
    api_keys: dict = field(default_factory=dict)
    fallback_providers: list = field(default_factory=list)

    @classmethod
    def from_system(cls, sys_config: dict) -> "SearchConfig":
        cfg = sys_config.get("search", {}) or {}
        api_key_env = cfg.get("api_key_env", "PARALLEL_API_KEY")
        api_key = os.environ.get(api_key_env, "")
        if cfg.get("provider", "parallel") == "parallel" and not api_key:
            logger.warning(
                "Environment variable '%s' not set; parallel search disabled",
                api_key_env,
            )
        after_date = cfg.get("after_date", "") or ""
        if not after_date and cfg.get("after_days"):
            after_date = (
                datetime.now() - timedelta(days=int(cfg["after_days"]))
            ).strftime("%Y-%m-%d")

        api_keys = {"parallel": api_key} if api_key else {}
        for provider, env_name in (cfg.get("api_key_envs") or {}).items():
            key = os.environ.get(env_name, "")
            if key:
                api_keys[provider] = key

        return cls(
            provider=cfg.get("provider", "parallel"),
            api_key=api_key,
            api_key_env=api_key_env,
            mode=cfg.get("mode", "advanced"),
            max_results=int(cfg.get("max_results", 10)),
            max_chars_per_result=int(cfg.get("max_chars_per_result", 3000)),
            max_chars_total=int(cfg.get("max_chars_total", 60000)),
            after_date=after_date,
            timeout=int(cfg.get("timeout", 60)),
            fallback_provider=cfg.get("fallback_provider", "duckduckgo"),
            api_keys=api_keys,
            fallback_providers=list(cfg.get("fallback_providers") or []),
        )


class SearchClient:
    """Facade over search providers with fallback and deduplication."""

    def __init__(
        self,
        config: SearchConfig,
        cancel_token: Optional[threading.Event] = None,
        progress_cb=None,
    ):
        self.config = config
        self._cancel = cancel_token or threading.Event()
        self._progress = progress_cb or (lambda _: None)
        self._session_id: str = ""
        self.calls = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def search(
        self,
        queries: list[str],
        objective: str = "",
        after_date: Optional[str] = None,
    ) -> list[SearchResult]:
        """Run a web search. Returns [] when all providers fail."""
        queries = self._clean_queries(queries)
        if not queries:
            logger.warning("search called without usable queries")
            return []

        if self._cancel.is_set():
            raise CancelledError("Cancelled before search")

        providers = [self.config.provider]
        for fallback in ([self.config.fallback_provider]
                         + list(self.config.fallback_providers)):
            if fallback and fallback not in providers:
                providers.append(fallback)

        for provider in providers:
            try:
                if provider == "parallel":
                    results = self._search_parallel(
                        queries, objective, after_date=after_date
                    )
                elif provider == "tavily":
                    results = self._search_tavily(queries, objective)
                elif provider == "brave":
                    results = self._search_brave(queries)
                elif provider == "serper":
                    results = self._search_serper(queries)
                elif provider == "duckduckgo":
                    results = self._search_duckduckgo(queries)
                else:
                    logger.error("Unknown search provider: %s", provider)
                    continue
                if results:
                    self.calls += 1
                    return self._dedupe(results)
                logger.warning(
                    "Search provider '%s' returned no results", provider
                )
            except CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - provider fallback
                logger.warning(
                    "Search provider '%s' failed: %s", provider, str(exc)[:300]
                )

        return []

    @staticmethod
    def format_results(results: list[SearchResult]) -> str:
        """Render results as a Markdown block for LLM consumption."""
        if not results:
            return "(no search results)"
        blocks = [r.to_markdown() for r in results]
        header = f"({len(results)} sources retrieved from web search)\n"
        return header + "\n\n---\n\n".join(blocks)

    # ------------------------------------------------------------------
    # Parallel Search API
    # ------------------------------------------------------------------

    def _search_parallel(
        self,
        queries: list[str],
        objective: str,
        after_date: Optional[str] = None,
    ) -> list[SearchResult]:
        cfg = self.config
        if not cfg.api_key:
            raise RuntimeError(
                f"missing API key (env {cfg.api_key_env})"
            )

        advanced: dict = {
            "max_results": cfg.max_results,
            "excerpt_settings": {
                "max_chars_per_result": cfg.max_chars_per_result,
            },
        }
        source_policy: dict = {}
        bound = after_date or cfg.after_date
        if bound:
            source_policy["after_date"] = bound
        if source_policy:
            advanced["source_policy"] = source_policy

        payload: dict = {
            "search_queries": [q[:_MAX_QUERY_CHARS] for q in queries],
            "mode": cfg.mode if cfg.mode in _ALLOWED_MODES else "advanced",
            "max_chars_total": cfg.max_chars_total,
            "advanced_settings": advanced,
        }
        if objective:
            payload["objective"] = objective[:_MAX_OBJECTIVE_CHARS]
        if self._session_id:
            payload["session_id"] = self._session_id

        self._progress(
            {"type": "search", "provider": "parallel", "queries": queries}
        )
        logger.info("Searching (parallel/%s): %s", cfg.mode, "; ".join(queries))

        body = self._post_json(_PARALLEL_ENDPOINT, payload, cfg.api_key)
        self._session_id = body.get("session_id", "") or self._session_id

        results = []
        for item in body.get("results", []):
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("publish_date"),
                    excerpts=list(item.get("excerpts") or []),
                    provider="parallel",
                )
            )
        usage = body.get("usage") or []
        if usage:
            logger.debug("Search usage: %s", usage)
        return results

    def _post_json(self, url: str, payload: dict, api_key: Optional[str] = None,
                   header_name: str = "x-api-key") -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BaseCodingCLi/2.0",
        }
        if api_key:
            headers[header_name] = api_key
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                req, timeout=self.config.timeout
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"connection failed: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # Tavily / Brave / Serper adapters
    # ------------------------------------------------------------------

    def _provider_key(self, provider: str) -> str:
        key = self.config.api_keys.get(provider, "")
        if not key and provider == self.config.provider:
            key = self.config.api_key
        if not key:
            raise RuntimeError(
                f"missing API key for provider '{provider}' "
                f"(configure search.api_key_envs.{provider})"
            )
        return key

    def _search_tavily(self, queries: list[str], objective: str) -> list[SearchResult]:
        key = self._provider_key("tavily")
        self._progress({"type": "search", "provider": "tavily", "queries": queries})
        logger.info("Searching (tavily): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            body = self._post_json(
                "https://api.tavily.com/search",
                {
                    "api_key": key,
                    "query": query,
                    "max_results": self.config.max_results,
                    "search_depth": "advanced",
                    "include_raw_content": False,
                },
                api_key=None,
            )
            for item in body.get("results", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("published_date"),
                    excerpts=[item.get("content") or ""],
                    provider="tavily",
                ))
        return results

    def _search_brave(self, queries: list[str]) -> list[SearchResult]:
        key = self._provider_key("brave")
        self._progress({"type": "search", "provider": "brave", "queries": queries})
        logger.info("Searching (brave): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            url = (
                "https://api.search.brave.com/res/v1/web/search?"
                f"q={urllib.parse.quote(query)}&count={self.config.max_results}"
            )
            body = self._get_json(url, headers={"X-Subscription-Token": key})
            for item in (body.get("web") or {}).get("results", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("url") or "",
                    publish_date=item.get("age"),
                    excerpts=[item.get("description") or ""],
                    provider="brave",
                ))
        return results

    def _search_serper(self, queries: list[str]) -> list[SearchResult]:
        key = self._provider_key("serper")
        self._progress({"type": "search", "provider": "serper", "queries": queries})
        logger.info("Searching (serper): %s", "; ".join(queries))

        results: list[SearchResult] = []
        for query in queries[:3]:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during search")
            body = self._post_json(
                "https://google.serper.dev/search",
                {"q": query, "num": self.config.max_results},
                api_key=key,
                header_name="X-API-KEY",
            )
            for item in body.get("organic", []):
                results.append(SearchResult(
                    title=item.get("title") or "",
                    url=item.get("link") or "",
                    publish_date=item.get("date"),
                    excerpts=[item.get("snippet") or ""],
                    provider="serper",
                ))
        return results

    def _get_json(self, url: str, headers: dict) -> dict:
        request = urllib.request.Request(
            url,
            headers={**headers, "User-Agent": "BaseCodingCLi/2.0"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"connection failed: {exc.reason}") from exc

    # ------------------------------------------------------------------
    # DuckDuckGo fallback (keyless)
    # ------------------------------------------------------------------

    def _search_duckduckgo(self, queries: list[str]) -> list[SearchResult]:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            try:
                from ddgs import DDGS  # type: ignore[no-redef]
            except ImportError:
                logger.warning(
                    "duckduckgo fallback unavailable: install duckduckgo-search"
                )
                return []

        self._progress(
            {"type": "search", "provider": "duckduckgo", "queries": queries}
        )
        logger.info("Searching (duckduckgo): %s", "; ".join(queries))

        results: list[SearchResult] = []
        with DDGS() as ddgs:
            for query in queries[:3]:
                if self._cancel.is_set():
                    raise CancelledError("Cancelled during search")
                for item in ddgs.text(query, max_results=self.config.max_results):
                    results.append(
                        SearchResult(
                            title=item.get("title") or "",
                            url=item.get("href") or item.get("url") or "",
                            excerpts=[item.get("body") or ""],
                            provider="duckduckgo",
                        )
                    )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_queries(queries: list[str]) -> list[str]:
        cleaned = []
        for query in queries:
            q = " ".join(str(query).split())
            if q and q not in cleaned:
                cleaned.append(q)
        return cleaned[:_MAX_QUERIES]

    @staticmethod
    def _dedupe(results: list[SearchResult]) -> list[SearchResult]:
        seen = set()
        deduped = []
        for result in results:
            key = result.url.rstrip("/").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(result)
        return deduped
