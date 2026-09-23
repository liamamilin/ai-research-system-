"""Tests for the embedding client."""

from __future__ import annotations

import json

import pytest

from core.embeddings import EmbeddingClient, EmbeddingError


class _Resp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen(captured, dim=3):
    def fake(request, timeout=None):
        body = json.loads(request.data.decode("utf-8"))
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["payload"] = body
        data = [
            {"index": i, "embedding": [float(i + 1)] * dim}
            for i in range(len(body["input"]))
        ]
        return _Resp(json.dumps({"data": data}).encode())
    return fake


def test_embed_sends_payload_and_parses(monkeypatch):
    captured = {}
    monkeypatch.setattr("core.embeddings.urllib.request.urlopen",
                        _fake_urlopen(captured))
    client = EmbeddingClient(model="m", base_url="https://api.test/v1",
                             api_key="sk-1")
    vectors = client.embed(["a", "b"])
    assert captured["url"] == "https://api.test/v1/embeddings"
    assert captured["auth"] == "Bearer sk-1"
    assert captured["payload"] == {"model": "m", "input": ["a", "b"]}
    assert vectors == [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
    assert client.calls == 1


def test_embed_uses_cache(monkeypatch):
    captured = {}
    monkeypatch.setattr("core.embeddings.urllib.request.urlopen",
                        _fake_urlopen(captured))
    client = EmbeddingClient(model="m", base_url="https://api.test/v1")
    client.embed(["a", "b"])
    client.embed(["a", "b", "c"])
    assert client.calls == 2
    assert captured["payload"]["input"] == ["c"]


def test_embed_batches_large_inputs(monkeypatch):
    captured = {}
    monkeypatch.setattr("core.embeddings.urllib.request.urlopen",
                        _fake_urlopen(captured))
    client = EmbeddingClient(model="m", base_url="https://api.test/v1")
    client.embed([f"t{i}" for i in range(70)])
    assert client.calls == 2


def test_embed_empty_returns_empty():
    client = EmbeddingClient(model="m", base_url="https://api.test/v1")
    assert client.embed([]) == []


def test_response_mismatch_raises(monkeypatch):
    def fake(request, timeout=None):
        return _Resp(json.dumps({"data": [{"index": 0, "embedding": [1.0]}]}).encode())

    monkeypatch.setattr("core.embeddings.urllib.request.urlopen", fake)
    client = EmbeddingClient(model="m", base_url="https://api.test/v1")
    with pytest.raises(EmbeddingError):
        client.embed(["a", "b"])


def test_from_system_requires_model(monkeypatch):
    monkeypatch.setenv("EMB_KEY_TEST", "k")
    with pytest.raises(EmbeddingError):
        EmbeddingClient.from_system({"ai": {"base_url": "https://x/v1"}})

    client = EmbeddingClient.from_system({
        "ai": {"embedding_model": "emb", "base_url": "https://x/v1",
               "api_key_env": "EMB_KEY_TEST"},
    })
    assert client.model == "emb"
    assert client.api_key == "k"
