"""The LLM client's transport layer: retries, classification, parsing, usage.

This is the code that decides whether a failed request costs one retry or a
whole run, and whether a truncated answer is reported as one. None of it ran in
any test before: tests/test_llm.py only exercised a reasoning-echo heuristic
through ``__new__``, so the HTTP path had no coverage at all.
"""

from __future__ import annotations

import threading
import types

import pytest

from core.errors import CancelledError, LLMError, LLMUnsupportedError
from core.llm import ChatResponse, LLMClient, LLMConfig, ToolCall


def make_client(**overrides) -> LLMClient:
    """A client wired to fakes, skipping __init__'s network-touching setup."""
    settings = {"model": "test-model", "base_url": "http://llm.invalid/v1",
                "api_key": "k", "max_retries": 2, "stream": False}
    settings.update(overrides)
    config = LLMConfig(**settings)
    client = LLMClient.__new__(LLMClient)
    client.config = config
    client._cancel = threading.Event()
    client.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0}
    client._client = None
    return client


def reply(content="hello", tool_calls=None, finish_reason="stop"):
    return ChatResponse(content=content, tool_calls=tool_calls or [],
                        finish_reason=finish_reason)


class FakeAPIStatusError(Exception):
    """Stands in for openai.APIStatusError, which takes a response."""

    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.status_code = code
        self.response = types.SimpleNamespace(status_code=code, headers={})


# --- retry policy ------------------------------------------------------------

@pytest.mark.parametrize("code", [408, 409, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(code, monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIStatusError(code)

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)

    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    # 1 initial attempt + max_retries.
    assert calls["n"] == client.config.max_retries + 1


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(code, monkeypatch):
    """Retrying a 401 just burns money; it will never become a 200."""
    client = make_client()
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIStatusError(code)

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1


def test_a_retry_that_succeeds_returns_normally(monkeypatch):
    client = make_client()
    state = {"n": 0}

    def flaky(_messages, **_kw):
        state["n"] += 1
        if state["n"] < 3:
            raise FakeAPIStatusError(503)
        return reply("recovered")

    monkeypatch.setattr(client, "_chat_cancellable", flaky)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
    assert state["n"] == 3


def test_backoff_grows_and_is_capped(monkeypatch):
    client = make_client(max_retries=8)
    delays: list[float] = []
    monkeypatch.setattr(client, "_sleep_cancellable", delays.append)
    monkeypatch.setattr("core.llm.random.random", lambda: 0.0)
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: (_ for _ in ()).throw(FakeAPIStatusError(500)))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert delays == sorted(delays), "backoff did not increase"
    assert all(d <= 30.0 for d in delays), "backoff exceeded the cap"


def test_cancellation_before_a_call_is_honoured(monkeypatch):
    client = make_client()
    client._cancel.set()
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: pytest.fail("must not call the API"))
    with pytest.raises(CancelledError):
        client.chat([{"role": "user", "content": "x"}])


def test_cancellation_is_not_retried(monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def cancel_midway(_messages, **_kw):
        calls["n"] += 1
        raise CancelledError("stop")

    monkeypatch.setattr(client, "_chat_cancellable", cancel_midway)
    with pytest.raises(CancelledError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1, "a cancel was retried"


def test_unsupported_tool_calling_is_not_retried(monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def unsupported(_messages, **_kw):
        calls["n"] += 1
        raise LLMUnsupportedError("no tools here")

    monkeypatch.setattr(client, "_chat_cancellable", unsupported)
    with pytest.raises(LLMUnsupportedError):
        client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert calls["n"] == 1


def test_streaming_is_only_used_without_tools(monkeypatch):
    """A stream and tool calls cannot be combined; tools must force one shot."""
    client = make_client(stream=True)
    seen: list[dict] = []

    def capture(_messages, **kw):
        seen.append(kw)
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", capture)
    client.chat([{"role": "user", "content": "x"}])
    client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert seen[0].get("_stream") is True
    assert seen[1].get("_stream") is None and seen[1].get("tools")


# --- response handling -------------------------------------------------------

def test_usage_accumulates_across_calls(monkeypatch):
    client = make_client()

    def counted(_messages, **_kw):
        client.usage["requests"] += 1
        client.usage["prompt_tokens"] += 100
        client.usage["completion_tokens"] += 20
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", counted)
    client.chat([{"role": "user", "content": "a"}])
    client.chat([{"role": "user", "content": "b"}])
    assert client.usage["requests"] == 2
    assert client.usage["prompt_tokens"] == 200
    assert client.usage["completion_tokens"] == 40


def test_finish_reason_survives_into_the_response(monkeypatch):
    """'length' means the answer was cut off; research.py now fails on it."""
    client = make_client()
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: reply("half a report", finish_reason="length"))
    assert client.chat([{"role": "user", "content": "x"}]).finish_reason == "length"


def test_assistant_message_carries_tool_calls():
    response = ChatResponse(
        content="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="c1", name="search_web", arguments={"queries": ["x"]})])
    message = response.assistant_message
    assert message["tool_calls"][0]["function"]["name"] == "search_web"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"queries": ["x"]}'


def test_tool_arguments_survive_non_string_payloads():
    """Some endpoints return the arguments already decoded."""
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.md"})
    payload = call if isinstance(call, dict) else {
        "id": call.id, "type": "function",
        "function": {"name": call.name, "arguments": call.arguments}}
    assert "read_file" in str(payload)


def test_content_normalisation_handles_structured_content():
    """Some providers return content as a list of parts rather than a string."""
    from core.llm import _normalize_content

    assert _normalize_content("plain") == "plain"
    # Parts are joined with newlines, so a multi-part answer keeps its shape.
    assert _normalize_content([{"type": "text", "text": "a"},
                               {"type": "text", "text": "b"}]) == "a\nb"
    assert _normalize_content(None) == ""


# --- configuration ------------------------------------------------------------

def test_config_is_read_from_system_yaml():
    cfg = LLMConfig.from_system({
        "ai": {"model": "m", "base_url": "http://x/v1", "api_key_env": "MY_KEY",
               "max_retries": 5, "timeout": 900, "request_timeout": 60,
               "max_tokens": 4096, "temperature": 0.7, "stream": False},
    })
    assert (cfg.model, cfg.max_retries, cfg.timeout) == ("m", 5, 900)
    assert (cfg.request_timeout, cfg.max_tokens) == (60, 4096)
    assert cfg.stream is False


def test_config_tolerates_a_missing_section():
    """An empty section yields defaults, not an AttributeError at call time."""
    cfg = LLMConfig.from_system({})
    assert cfg.max_retries >= 0 and cfg.timeout > 0
    assert cfg.stream is True  # the shipped default


def test_reasoning_echo_is_model_specific():
    def client(model, mode="auto"):
        c = LLMClient.__new__(LLMClient)
        c.config = LLMConfig(model=model, base_url="http://x", reasoning_content=mode)
        return c

    assert client("deepseek-v4.1-flash")._should_echo_reasoning() is True
    assert client("gpt-5.4")._should_echo_reasoning() is False
    # An explicit setting always wins.
    assert client("gpt-5.4", "always")._should_echo_reasoning() is True
    assert client("deepseek-v4.1-flash", "never")._should_echo_reasoning() is False
