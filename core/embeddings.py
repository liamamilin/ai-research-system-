"""Embedding client for OpenAI-compatible ``/embeddings`` endpoints.

Configured via ``system.yaml``::

    ai:
      embedding_model: "text-embedding-3-small"   # empty = semantic features off
      base_url: "https://api.openai.com/v1"
      api_key_env: "LLM_API_KEY"

Embeddings may live on a different endpoint than the chat model::

    ai:
      embedding_model: "bge-m3:latest"
      embedding_base_url: "http://localhost:11434/v1"
      embedding_api_key_env: ""   # local Ollama needs no key
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger(__name__)

_BATCH = 64


class EmbeddingError(RuntimeError):
    pass


class EmbeddingClient:
    """Minimal sync client with an in-memory cache."""

    def __init__(self, model: str, base_url: str, api_key: str = "",
                 timeout: int = 60):
        if not model:
            raise EmbeddingError("ai.embedding_model is not configured")
        if not base_url:
            raise EmbeddingError("ai.base_url is not configured")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.calls = 0
        self._cache: dict[str, list[float]] = {}

    @classmethod
    def from_system(cls, sys_config: dict) -> "EmbeddingClient":
        ai = sys_config.get("ai") or {}
        model = (ai.get("embedding_model") or "").strip()
        if not model:
            raise EmbeddingError("ai.embedding_model is not configured")
        base_url = (ai.get("embedding_base_url") or ai.get("base_url") or "").strip()
        if "embedding_api_key_env" in ai:
            api_key_env = ai.get("embedding_api_key_env") or ""
        else:
            api_key_env = ai.get("api_key_env") or ""
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        return cls(
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=int(ai.get("embedding_timeout", 60)),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, using the cache and batching requests."""
        if not texts:
            return []
        missing = [t for t in texts if t not in self._cache]
        for i in range(0, len(missing), _BATCH):
            batch = missing[i:i + _BATCH]
            for text, vector in zip(batch, self._embed_batch(batch)):
                self._cache[text] = vector
        return [self._cache[t] for t in texts]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": texts}).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "User-Agent": "BaseCodingCLi-Embeddings/1.0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            f"{self.base_url}/embeddings", data=payload, headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:200]
            raise EmbeddingError(f"embeddings HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            raise EmbeddingError(f"embeddings request failed: {exc}") from exc

        self.calls += 1
        items = data.get("data") or []
        items = sorted(items, key=lambda item: item.get("index", 0))
        vectors = [item.get("embedding") or [] for item in items]
        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embeddings response mismatch: {len(vectors)} != {len(texts)}")
        return vectors
