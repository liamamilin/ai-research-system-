"""Setup status endpoint tests."""

from __future__ import annotations


def test_setup_status_public_and_reports_no_admin(client, web_env):
    from web.auth import db as user_db

    for user in user_db.list_users():
        user_db.delete_user(user["id"])

    r = client.get("/api/setup/status")
    assert r.status_code == 200
    body = r.json()
    assert body["needs_admin"] is True
    assert "llm_configured" in body


def test_setup_status_with_users(client, web_env):
    r = client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["needs_admin"] is False


def test_setup_status_llm_flag(client, web_env, monkeypatch):
    tmp_path, _ = web_env
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n  model: \"test-model\"\n  api_key_env: \"SETUP_TEST_KEY\"\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SETUP_TEST_KEY", raising=False)
    assert client.get("/api/setup/status").json()["llm_configured"] is False

    monkeypatch.setenv("SETUP_TEST_KEY", "k")
    assert client.get("/api/setup/status").json()["llm_configured"] is True
