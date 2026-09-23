"""Tests for API-key management and per-service connectivity tests."""

from __future__ import annotations

import pytest


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _write_config(tmp_path, search_provider="parallel"):
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n"
        "  model: \"test-model\"\n"
        "  base_url: \"https://llm.test/v1\"\n"
        "  api_key_env: \"LLM_API_KEY\"\n"
        "  embedding_model: \"emb-model\"\n"
        "  embedding_base_url: \"http://localhost:11434/v1\"\n"
        "  embedding_api_key_env: \"EMBEDDING_API_KEY\"\n"
        "search:\n"
        f"  provider: \"{search_provider}\"\n"
        "  api_key_env: \"PARALLEL_API_KEY\"\n"
        "  api_key_envs:\n"
        "    parallel: \"PARALLEL_API_KEY\"\n"
        "    tavily: \"TAVILY_API_KEY\"\n"
        "extraction:\n"
        "  api_key_env: \"EXTRACTION_API_KEY\"\n",
        encoding="utf-8",
    )


@pytest.fixture()
def secrets_env(web_env, monkeypatch):
    tmp_path, _ = web_env
    _write_config(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text('LLM_API_KEY="old-llm-key"\nPARALLEL_API_KEY="parallel-secret"\n',
                        encoding="utf-8")
    monkeypatch.setattr("web.routes.config._ENV_PATH_OVERRIDE", str(env_file))
    monkeypatch.setenv("LLM_API_KEY", "old-llm-key")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-secret")
    for name in ("EMBEDDING_API_KEY", "TAVILY_API_KEY", "EXTRACTION_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return env_file


def test_secrets_list_masks_values(client, secrets_env):
    _login(client)
    r = client.get("/api/config/secrets")
    assert r.status_code == 200
    items = {item["env"]: item for item in r.json()["secrets"]}
    assert items["LLM_API_KEY"]["configured"] is True
    assert items["LLM_API_KEY"]["masked"] == "old-****ey"
    assert items["TAVILY_API_KEY"]["configured"] is False
    assert items["EMBEDDING_API_KEY"]["label"] == "Embedding 模型"
    assert "old-llm-key" not in r.text


def test_secrets_requires_admin(client, secrets_env):
    assert client.get("/api/config/secrets").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/config/secrets").status_code == 403


def test_secrets_update_writes_env_file(client, secrets_env, monkeypatch):
    _login(client)
    r = client.put("/api/config/secrets",
                   json={"values": {"TAVILY_API_KEY": "tv-secret", "LLM_API_KEY": None}},
                   headers=_csrf(client))
    assert r.status_code == 200

    text = secrets_env.read_text(encoding="utf-8")
    assert 'TAVILY_API_KEY="tv-secret"' in text
    assert "LLM_API_KEY" not in text
    assert 'PARALLEL_API_KEY="parallel-secret"' in text
    import os
    assert oct(os.stat(secrets_env).st_mode)[-3:] == "600"
    items = {item["env"]: item for item in r.json()["secrets"]}
    assert items["TAVILY_API_KEY"]["configured"] is True
    assert items["LLM_API_KEY"]["configured"] is False


def test_secrets_update_rejects_unknown_and_bad_values(client, secrets_env):
    _login(client)
    r = client.put("/api/config/secrets",
                   json={"values": {"SOMETHING_ELSE": "x"}}, headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_secret"

    r2 = client.put("/api/config/secrets",
                    json={"values": {"TAVILY_API_KEY": "a\nb"}}, headers=_csrf(client))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_value"

    r3 = client.put("/api/config/secrets", json={"values": []}, headers=_csrf(client))
    assert r3.status_code == 422


def test_secrets_update_requires_csrf(client, secrets_env):
    _login(client)
    r = client.put("/api/config/secrets", json={"values": {"TAVILY_API_KEY": "x"}})
    assert r.status_code == 403


def test_test_llm_uses_unsaved_overrides_and_key(client, secrets_env, monkeypatch):
    captured = {}

    class FakeLLMClient:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def chat(self, messages, **kwargs):
            class R:
                content = "ok"
            return R()

        def close(self):
            pass

    monkeypatch.setattr("web.routes.config.LLMClient", FakeLLMClient)
    _login(client)

    r = client.post("/api/config/test-llm",
                    json={"config": {"ai": {"model": "form-model"}},
                          "api_key": "typed-key"},
                    headers=_csrf(client))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "form-model"
    assert captured["cfg"].api_key == "typed-key"


def test_test_llm_reports_failure(client, secrets_env, monkeypatch):
    class Boom:
        def __init__(self, cfg):
            pass

        def chat(self, *a, **k):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr("web.routes.config.LLMClient", Boom)
    _login(client)
    body = client.post("/api/config/test-llm", json={}, headers=_csrf(client)).json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]


def test_test_search_uses_unsaved_overrides(client, secrets_env, monkeypatch):
    captured = {}

    class FakeSearchClient:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def search(self, queries, objective=""):
            class R:
                url = "https://hit.test/1"
            return [R()]

    monkeypatch.setattr("web.routes.config.SearchClient", FakeSearchClient)
    _login(client)

    body = client.post("/api/config/test-search",
                       json={"config": {"search": {"provider": "tavily"}},
                             "api_key": "tv-typed"},
                       headers=_csrf(client)).json()
    assert body["ok"] is True
    assert body["provider"] == "tavily"
    assert body["sample_url"] == "https://hit.test/1"
    assert captured["cfg"].api_keys["tavily"] == "tv-typed"


def test_test_embedding(client, secrets_env, monkeypatch):
    class FakeClient:
        model = "emb-model"
        base_url = "http://localhost:11434/v1"
        api_key = ""

        def embed(self, texts):
            return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr("core.embeddings.EmbeddingClient.from_system",
                        classmethod(lambda cls, cfg: FakeClient()))
    _login(client)
    body = client.post("/api/config/test-embedding", json={}, headers=_csrf(client)).json()
    assert body["ok"] is True
    assert body["dims"] == 3


def test_test_embedding_failure(client, secrets_env, monkeypatch):
    def boom(cls, cfg):
        raise RuntimeError("embedding_model is not configured")

    monkeypatch.setattr("core.embeddings.EmbeddingClient.from_system", classmethod(boom))
    _login(client)
    body = client.post("/api/config/test-embedding", json={}, headers=_csrf(client)).json()
    assert body["ok"] is False
    assert "embedding_model" in body["error"]


def test_test_endpoints_require_admin(client, secrets_env):
    _login(client, "viewer", "viewer-pass-123")
    for path in ("test-llm", "test-search", "test-embedding"):
        assert client.post(f"/api/config/{path}", json={}, headers=_csrf(client)).status_code == 403
