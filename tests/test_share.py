"""Share link tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


@pytest.fixture()
def report(web_env):
    tmp_path, _ = web_env
    path = tmp_path / "output" / "demo" / "r.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 标题\n\n正文内容", encoding="utf-8")
    return "demo/r.md"


def test_create_share_and_read_without_auth(client, report, app):
    _login(client, "editor", "editor-pass-123")
    r = client.post("/api/reports/share", json={"path": report},
                    headers=_csrf(client))
    assert r.status_code == 200
    token = r.json()["token"]
    assert r.json()["url"] == f"/share/{token}"

    anon = TestClient(app)
    shared = anon.get(f"/api/share/{token}")
    assert shared.status_code == 200
    body = shared.json()
    assert body["path"] == report
    assert "正文内容" in body["content"]
    assert body["shared_by"] == "editor"


def test_share_requires_editor_and_csrf(client, report):
    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/reports/share", json={"path": report},
                    headers=_csrf(client))
    assert r.status_code == 403

    _login(client)
    r2 = client.post("/api/reports/share", json={"path": report})
    assert r2.status_code == 403
    assert r2.json()["error"]["code"] == "csrf_failed"


def test_share_unknown_path(client, report):
    _login(client)
    r = client.post("/api/reports/share", json={"path": "nope.md"},
                    headers=_csrf(client))
    assert r.status_code == 404


def test_share_traversal_rejected(client, report):
    _login(client)
    r = client.post("/api/reports/share", json={"path": "../../etc/passwd"},
                    headers=_csrf(client))
    assert r.status_code in (400, 404)


def test_invalid_and_expired_tokens(client, report, app):
    from web import share as share_mod

    anon = TestClient(app)
    assert anon.get("/api/share/not-a-token").status_code == 401

    expired, _ = share_mod.create_share_token(report, ttl_hours=1)
    payload = share_mod.verify_share_token(expired)
    assert payload is not None

    import jwt
    from web.settings import get_settings
    settings = get_settings()
    stale = jwt.encode({"scope": "report-share", "path": report,
                        "exp": 1}, settings.auth.secret_key, algorithm="HS256")
    assert anon.get(f"/api/share/{stale}").status_code == 401

    wrong_scope = jwt.encode({"scope": "access", "path": report,
                              "exp": 4102444800}, settings.auth.secret_key,
                             algorithm="HS256")
    assert anon.get(f"/api/share/{wrong_scope}").status_code == 401


def test_ttl_validation(client, report):
    _login(client)
    r = client.post("/api/reports/share", json={"path": report, "ttl_hours": "abc"},
                    headers=_csrf(client))
    assert r.status_code == 422

    r2 = client.post("/api/reports/share", json={"path": report, "ttl_hours": 1},
                     headers=_csrf(client))
    assert r2.status_code == 200
