"""Tests for Q&A retrieval and the /api/qa routes."""

from __future__ import annotations

import pytest

from core import qa
from web.indexer import db as index_db
from web.indexer import vectors


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


class FakeLLM:
    def __init__(self, content="回答 [1]", usage=None):
        self.content = content
        self.usage = usage or {"total_tokens": 42}
        self.closed = False

    def chat(self, messages, **kwargs):
        class R:
            pass

        r = R()
        r.content = self.content
        r.usage = self.usage
        r.messages = messages
        return r

    def close(self):
        self.closed = True


def test_build_messages_numbers_sources():
    hits = [
        {"path": "a.md", "snippet": "alpha body", "score": 0.9},
        {"path": "b.md", "snippet": "beta body", "score": 0.5},
    ]
    messages = qa.build_messages("what is alpha?", hits)
    user = messages[-1]["content"]
    assert "[1] 来源：a.md" in user
    assert "[2] 来源：b.md" in user
    assert "what is alpha?" in user


def test_ask_returns_answer_and_citations():
    hits = [{"path": "a.md", "title": "A", "snippet": "body", "score": 0.7,
             "source": "hybrid"}]
    result = qa.ask("q", hits, FakeLLM(content="答案 [1]"))
    assert result["answer"] == "答案 [1]"
    assert result["citations"][0]["index"] == 1
    assert result["citations"][0]["path"] == "a.md"
    assert result["usage"]["total_tokens"] == 42


def test_ask_with_fallback_handles_failure():
    def boom():
        raise RuntimeError("no llm")

    assert qa.ask_with_fallback("q", [], boom) is None


@pytest.fixture()
def indexed(web_env):
    tmp_path, _ = web_env
    index_db.init_db()
    vectors.init_vectors()
    for rel, body in (("a.md", "alpha content"), ("b.md", "beta content")):
        path = tmp_path / "output" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        index_db.upsert_report(rel, mtime=1.0, title=rel, content=body)
    return tmp_path


def test_qa_requires_auth(client):
    assert client.post("/api/qa", json={"question": "hi"}).status_code == 401


def test_qa_validation(client, indexed):
    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/qa", json={"question": "x"}, headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_question"


def test_qa_fts_only_answers(client, indexed, monkeypatch):
    from web.routes import qa as qa_route

    monkeypatch.setattr(qa_route, "_embed_query", lambda q, cfg: [])
    fake = FakeLLM(content="alpha 的回答 [1]")
    monkeypatch.setattr(qa_route, "_llm_factory", lambda cfg: (lambda: fake))

    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/qa", json={"question": "alpha"}, headers=_csrf(client))
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "fts"
    assert body["answer"] == "alpha 的回答 [1]"
    assert body["citations"][0]["path"] == "a.md"
    assert fake.closed is True


def test_qa_no_hits_message(client, indexed, monkeypatch):
    from web.routes import qa as qa_route

    monkeypatch.setattr(qa_route, "_embed_query", lambda q, cfg: [])
    _login(client)
    r = client.post("/api/qa", json={"question": "zzz-nothing"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["citations"] == []


def test_qa_llm_failure_returns_502(client, indexed, monkeypatch):
    from web.routes import qa as qa_route

    monkeypatch.setattr(qa_route, "_embed_query", lambda q, cfg: [])

    def boom_factory(cfg):
        def make():
            raise RuntimeError("endpoint down")
        return make

    monkeypatch.setattr(qa_route, "_llm_factory", boom_factory)
    _login(client)
    r = client.post("/api/qa", json={"question": "alpha"}, headers=_csrf(client))
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "llm_failed"


def test_reindex_requires_configuration(client, indexed):
    _login(client)
    r = client.post("/api/qa/reindex", headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "embeddings_not_configured"


def test_reindex_embeds_reports(client, indexed, monkeypatch):
    from core import embeddings
    from web.routes import qa as qa_route

    class FakeClient:
        model = "fake-embed"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embeddings.EmbeddingClient, "from_system",
                        classmethod(lambda cls, cfg: FakeClient()))

    _login(client)
    r = client.post("/api/qa/reindex", headers=_csrf(client))
    assert r.status_code == 200
    body = r.json()
    assert body["embedded"] == 2
    assert body["stats"]["chunks"] == 2

    r2 = client.post("/api/qa/reindex", headers=_csrf(client))
    assert r2.json()["skipped"] == 2


def test_qa_records_usage_for_the_budget(client, indexed, monkeypatch):
    """A Q&A call must land in the usage ledger.

    The route used to call StateManager.use_state_dir(...), which is a module
    function, not a class method. The AttributeError was swallowed by a blanket
    except, so every answer returned 200 and recorded nothing — the monthly
    budget could not see Q&A spending at all.
    """
    import json
    import os

    from web.routes import qa as qa_route

    monkeypatch.setattr(qa_route, "_embed_query", lambda q, cfg: [])
    fake = FakeLLM(usage={"prompt_tokens": 30, "completion_tokens": 12,
                          "total_tokens": 42})
    monkeypatch.setattr(qa_route, "_llm_factory", lambda cfg: (lambda: fake))

    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/qa", json={"question": "alpha"}, headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["usage"]["total_tokens"] == 42

    state_dir = os.path.join(str(indexed), "state")
    ledger = os.path.join(state_dir, "history", "__usage__.jsonl")
    assert os.path.isfile(ledger), "no usage ledger written for a successful answer"
    rows = [json.loads(line) for line in open(ledger, encoding="utf-8") if line.strip()]
    assert rows, "usage ledger is empty"
    assert rows[-1]["job_name"] == "__qa__"
    assert rows[-1]["user"] == "viewer"
    assert rows[-1]["usage"]["total_tokens"] == 42
