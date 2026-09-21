"""Tests for the LLM client's DeepSeek reasoning-content echo handling."""

from __future__ import annotations

from core.llm import ChatResponse, LLMClient, LLMConfig, ToolCall


def _client(model: str, reasoning_content: str = "auto") -> LLMClient:
    client = LLMClient.__new__(LLMClient)  # skip __init__/network
    client.config = LLMConfig(model=model, base_url="http://x",
                              reasoning_content=reasoning_content)
    return client


def test_echo_heuristic_deepseek_only():
    assert _client("deepseek-v4.1-flash")._should_echo_reasoning() is True
    assert _client("gpt-5.4")._should_echo_reasoning() is False
    assert _client("gpt-5.4", "always")._should_echo_reasoning() is True
    assert _client("deepseek-v4-pro", "never")._should_echo_reasoning() is False


def test_assistant_message_includes_reasoning_when_echoing():
    resp = ChatResponse(
        content="",
        tool_calls=[ToolCall(id="c1", name="search_web", arguments={"queries": ["x"]})],
        reasoning_content="thinking...",
        echo_reasoning=True,
    )
    msg = resp.assistant_message
    assert msg["reasoning_content"] == "thinking..."
    assert msg["tool_calls"][0]["function"]["name"] == "search_web"


def test_assistant_message_omits_reasoning_otherwise():
    resp = ChatResponse(content="hi", reasoning_content="thinking...",
                        echo_reasoning=False)
    assert "reasoning_content" not in resp.assistant_message


def test_config_from_system_reads_reasoning_mode():
    cfg = LLMConfig.from_system(
        {"ai": {"model": "deepseek-v4-pro", "base_url": "http://x",
                "reasoning_content": "always"}}
    )
    assert cfg.reasoning_content == "always"
    assert cfg.model == "deepseek-v4-pro"
