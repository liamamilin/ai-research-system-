"""
LLM API client - OpenAI-compatible chat completions.

Direct HTTP API access (no CLI subprocess). Supports tool calling for the
research agent, cancellable calls, retries with backoff, and usage tracking.

Configured via ``system.yaml``::

    ai:
      model: "qwen3.8:27b-mlx"
      base_url: "http://localhost:11434/v1"
      api_key_env: "LLM_API_KEY"   # optional for local endpoints
      timeout: 1800                # per-request wall clock ceiling (seconds)
      request_timeout: 600         # single HTTP attempt (seconds)
      max_tokens: 32768
      temperature: 0.3
      max_retries: 3
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import CancelledError, LLMConfigError, LLMError, LLMUnsupportedError

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI

    _OPENAI_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency guard
    OpenAI = None  # type: ignore[assignment]
    _OPENAI_AVAILABLE = False

_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
_RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "RateLimitError",
}


@dataclass
class LLMConfig:
    """LLM endpoint configuration."""

    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout: int = 1800
    request_timeout: int = 600
    max_tokens: int = 32768
    temperature: float = 0.3
    max_retries: int = 3
    reasoning_content: str = "auto"   # auto | always | never (DeepSeek echo)
    stream: bool = True               # stream text-only calls (live progress)
    extra_body: dict = field(default_factory=dict)
    extra_headers: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.model:
            raise LLMConfigError("ai.model is not configured in system.yaml")
        if not self.base_url:
            raise LLMConfigError("ai.base_url is not configured in system.yaml")

    @classmethod
    def from_system(cls, sys_config: dict, section: str = "ai") -> "LLMConfig":
        """Build a config from a system.yaml mapping (typically the ``ai`` key)."""
        cfg = sys_config.get(section, {}) or {}
        api_key_env = cfg.get("api_key_env")
        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not api_key:
            logger.warning(
                "Environment variable '%s' not set; requesting without auth "
                "(fine for local endpoints, will fail for hosted ones)",
                api_key_env,
            )
        return cls(
            model=cfg.get("model", ""),
            base_url=(cfg.get("base_url") or "").rstrip("/"),
            api_key=api_key,
            timeout=int(cfg.get("timeout", 1800)),
            request_timeout=int(cfg.get("request_timeout", 600)),
            max_tokens=int(cfg.get("max_tokens", 32768)),
            temperature=float(cfg.get("temperature", 0.3)),
            max_retries=int(cfg.get("max_retries", 3)),
            reasoning_content=str(cfg.get("reasoning_content", "auto")),
            stream=bool(cfg.get("stream", True)),
            extra_body=cfg.get("extra_body") or {},
            extra_headers=cfg.get("extra_headers") or {},
        )


@dataclass
class ToolCall:
    """A single tool call requested by the model."""

    id: str
    name: str
    arguments: dict


@dataclass
class ChatResponse:
    """Normalized chat completion response."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    reasoning_content: str = ""
    echo_reasoning: bool = False

    @property
    def assistant_message(self) -> dict:
        """Message dict suitable for appending to the conversation history.

        DeepSeek thinking mode requires the assistant message to carry back
        ``reasoning_content`` when it contains tool calls; other providers
        ignore/reject unknown message fields, so echoing is opt-in.
        """
        msg: dict[str, Any] = {"role": "assistant"}
        if self.content:
            msg["content"] = self.content
        if self.tool_calls:
            msg["content"] = self.content or ""
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.echo_reasoning and self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        return msg


def _normalize_content(content: Any) -> str:
    """Flatten string or multimodal content parts into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    parts.append(str(text))
            elif item:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


class LLMClient:
    """OpenAI-compatible chat client with retries and cancellation."""

    def __init__(
        self,
        config: LLMConfig,
        cancel_token: Optional[threading.Event] = None,
        progress_cb=None,
    ):
        if not _OPENAI_AVAILABLE:
            raise LLMConfigError(
                "The 'openai' package is required for LLM API calls. "
                "Install it with: pip install openai"
            )
        config.validate()
        self.config = config
        self._cancel = cancel_token or threading.Event()
        self._progress = progress_cb or (lambda _: None)
        self._session_id = "ses_" + uuid.uuid4().hex
        self._client = self._make_client()
        self.usage = {
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        """Run one chat completion, with retries and cancellation.

        Raises ``CancelledError`` when the cancel token is set and
        ``LLMUnsupportedError`` when the endpoint rejects tool calling.
        """
        attempt = 0
        while True:
            if self._cancel.is_set():
                raise CancelledError("Cancelled before LLM call")

            try:
                if self.config.stream and not tools:
                    return self._chat_cancellable(
                        messages,
                        _stream=True,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                return self._chat_cancellable(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except (CancelledError, LLMUnsupportedError):
                raise
            except Exception as exc:
                if not self._is_retryable(exc):
                    raise self._classify(exc) from exc
                attempt += 1
                if attempt > self.config.max_retries:
                    raise LLMError(
                        f"LLM call failed after {attempt} attempts: {exc}"
                    ) from exc
                delay = min(2 ** attempt + random.random(), 30.0)
                logger.warning(
                    "LLM call failed (attempt %d/%d): %s — retrying in %.1fs",
                    attempt,
                    self.config.max_retries + 1,
                    self._short(exc),
                    delay,
                )
                self._sleep_cancellable(delay)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            self._client.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _make_client(self):
        return OpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key or "not-needed",
            timeout=self.config.request_timeout,
            max_retries=0,
            default_headers=self._resolved_headers(),
        )

    def _resolved_headers(self) -> Optional[dict]:
        """Resolve header placeholders such as ``{session_id}``.

        A stable session id per client lets gateways (e.g. OpenCode Go)
        optimize routing and prompt caching for one job conversation.
        """
        if not self.config.extra_headers:
            return None
        headers = {}
        for key, value in self.config.extra_headers.items():
            headers[key] = str(value).replace("{session_id}", self._session_id)
        return headers

    def _ensure_client(self):
        if self._client is None:
            self._client = self._make_client()
        return self._client

    def _chat_cancellable(self, messages, **kwargs) -> ChatResponse:
        """Run the blocking HTTP call in a worker thread so cancellation can
        abort it by closing the HTTP client."""
        box: dict[str, Any] = {}

        def worker():
            try:
                if kwargs.pop("_stream", False):
                    box["resp"] = self._chat_once_stream(messages, **kwargs)
                else:
                    box["resp"] = self._chat_once(messages, **kwargs)
            except BaseException as exc:  # noqa: BLE001 - re-raised in caller
                box["err"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        start = time.time()
        deadline = start + self.config.timeout
        last_beat = start
        heartbeat_interval = 15.0
        while thread.is_alive():
            if self._cancel.is_set():
                self._abort()
                thread.join(timeout=2)
                raise CancelledError("LLM call cancelled")
            now = time.time()
            if now > deadline:
                self._abort()
                thread.join(timeout=2)
                raise LLMError(
                    f"LLM call exceeded timeout of {self.config.timeout}s"
                )
            if now - last_beat >= heartbeat_interval:
                self._progress(
                    {"type": "heartbeat", "elapsed": int(now - start)}
                )
                last_beat = now
            thread.join(timeout=0.25)

        if "err" in box:
            raise box["err"]
        return box["resp"]

    def _chat_once_stream(self, messages, temperature=None,
                          max_tokens=None) -> ChatResponse:
        """Streaming variant for tool-free calls (e.g. report generation).

        Emits throttled ``generating`` progress events and accumulates text
        and reasoning deltas. Falls back gracefully when the endpoint does
        not support ``stream_options``.
        """
        client = self._ensure_client()
        base = {
            "model": self.config.model,
            "messages": messages,
            "temperature": (
                self.config.temperature if temperature is None else temperature
            ),
            "max_tokens": (
                self.config.max_tokens if max_tokens is None else max_tokens
            ),
        }

        stream = None
        try:
            stream = client.chat.completions.create(
                **base, stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as exc:  # noqa: BLE001 - retry without usage option
            if "stream_options" not in self._short(exc, 500).lower():
                raise
            logger.debug("Endpoint rejected stream_options; retrying without it")
            stream = client.chat.completions.create(**base, stream=True)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        usage: dict = {}
        finish_reason = ""
        started = time.time()
        deadline = started + self.config.timeout
        last_emit = started
        emit_interval = 2.0

        try:
            for chunk in stream:
                if self._cancel.is_set():
                    raise CancelledError("LLM streaming cancelled")
                now = time.time()
                if now > deadline:
                    raise LLMError(
                        f"LLM call exceeded timeout of {self.config.timeout}s"
                    )

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = {
                        "prompt_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                        "completion_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
                        "total_tokens": getattr(chunk_usage, "total_tokens", 0) or 0,
                    }

                choices = getattr(chunk, "choices", None) or []
                if choices:
                    choice = choices[0]
                    if getattr(choice, "finish_reason", None):
                        finish_reason = choice.finish_reason
                    delta = getattr(choice, "delta", None)
                    if delta is not None:
                        text = getattr(delta, "content", None)
                        if text:
                            content_parts.append(text)
                        reasoning = getattr(delta, "reasoning_content", None)
                        if reasoning is None:
                            extra = getattr(delta, "model_extra", None) or {}
                            reasoning = extra.get("reasoning_content")
                        if reasoning:
                            reasoning_parts.append(str(reasoning))

                if now - last_emit >= emit_interval:
                    self._progress({
                        "type": "generating",
                        "chars": sum(len(p) for p in content_parts),
                        "reasoning_chars": sum(len(p) for p in reasoning_parts),
                        "elapsed": int(now - started),
                    })
                    last_emit = now
        finally:
            try:
                stream.close()
            except Exception:
                pass

        content = "".join(content_parts)
        if usage:
            self.usage["requests"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.usage[key] += usage.get(key, 0)
        elif content:
            # Endpoint omitted usage: approximate so budgets stay meaningful
            approx = max(1, len(content) // 4)
            usage = {"prompt_tokens": 0, "completion_tokens": approx,
                     "total_tokens": approx, "estimated": True}
            self.usage["requests"] += 1
            self.usage["completion_tokens"] += approx
            self.usage["total_tokens"] += approx

        return ChatResponse(
            content=content,
            tool_calls=[],
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts),
            echo_reasoning=self._should_echo_reasoning(),
        )

    def _chat_once(self, messages, tools=None, tool_choice=None,
                   temperature=None, max_tokens=None) -> ChatResponse:
        client = self._ensure_client()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": (
                self.config.temperature if temperature is None else temperature
            ),
            "max_tokens": (
                self.config.max_tokens if max_tokens is None else max_tokens
            ),
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        if self.config.extra_body:
            kwargs["extra_body"] = self.config.extra_body

        raw = client.chat.completions.create(**kwargs)
        return self._parse_response(raw)

    def _parse_response(self, raw) -> ChatResponse:
        usage = getattr(raw, "usage", None)
        usage_dict = {}
        if usage is not None:
            usage_dict = {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            }
            self.usage["requests"] += 1
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                self.usage[key] += usage_dict.get(key, 0)

        if not getattr(raw, "choices", None):
            raise LLMError("LLM returned no choices")

        choice = raw.choices[0]
        message = choice.message
        tool_calls: list[ToolCall] = []
        for tc in getattr(message, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            arguments: dict = {}
            raw_args = getattr(fn, "arguments", "") or ""
            if isinstance(raw_args, dict):
                arguments = raw_args
            else:
                try:
                    arguments = json.loads(raw_args) if raw_args.strip() else {}
                except json.JSONDecodeError:
                    logger.warning("Malformed tool arguments: %s", raw_args[:200])
                    arguments = {}
            tool_calls.append(
                ToolCall(
                    id=getattr(tc, "id", "") or f"call_{len(tool_calls)}",
                    name=getattr(fn, "name", "") or "",
                    arguments=arguments,
                )
            )

        reasoning = getattr(message, "reasoning_content", None)
        if reasoning is None:
            extra = getattr(message, "model_extra", None) or {}
            reasoning = extra.get("reasoning_content")

        return ChatResponse(
            content=_normalize_content(getattr(message, "content", "")),
            tool_calls=tool_calls,
            finish_reason=getattr(choice, "finish_reason", "") or "",
            usage=usage_dict,
            reasoning_content=_normalize_content(reasoning),
            echo_reasoning=self._should_echo_reasoning(),
        )

    def _should_echo_reasoning(self) -> bool:
        """Whether to send reasoning_content back on assistant messages.

        ``auto`` enables it for DeepSeek models, whose thinking mode returns
        400 ('reasoning_content ... must be passed back') otherwise.
        """
        mode = (self.config.reasoning_content or "auto").lower()
        if mode == "always":
            return True
        if mode == "never":
            return False
        return "deepseek" in (self.config.model or "").lower()

    def _abort(self) -> None:
        """Force-close the HTTP client to abort an in-flight request."""
        try:
            self._client.close()
        except Exception:
            pass
        self._client = None

    def _is_retryable(self, exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status in _RETRYABLE_STATUS:
            return True
        return type(exc).__name__ in _RETRYABLE_NAMES

    def _classify(self, exc: Exception) -> LLMError:
        """Map provider errors into our error hierarchy."""
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return LLMError(f"LLM request timed out: {exc}")

        status = getattr(exc, "status_code", None)
        text = self._short(exc)
        lowered = text.lower()
        if status == 400 and (
            "tool" in lowered or "function" in lowered or "tool_choice" in lowered
        ):
            return LLMUnsupportedError(
                f"Endpoint/model does not support tool calling: {text}"
            )
        if status in (401, 403):
            return LLMError(f"LLM auth failed ({status}): {text}")
        if status == 402 or "insufficient balance" in lowered or "credit" in lowered:
            return LLMError(
                f"LLM account has insufficient credits/balance: {text}"
            )
        if status == 404:
            return LLMError(
                f"LLM model or endpoint not found (404): {text}. "
                f"Check ai.model='{self.config.model}' and ai.base_url="
                f"'{self.config.base_url}'"
            )
        return LLMError(f"LLM call failed: {text}")

    @staticmethod
    def _short(exc: Exception, limit: int = 300) -> str:
        return str(exc).replace("\n", " ")[:limit]

    def _sleep_cancellable(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if self._cancel.is_set():
                raise CancelledError("Cancelled during retry backoff")
            time.sleep(min(0.25, end - time.time()))
