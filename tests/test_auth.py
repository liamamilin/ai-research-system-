"""Authentication, CSRF, RBAC and API-token tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> str | None:
    return client.cookies.get("ai_research_csrf")


def _csrf_header(client) -> dict:
    return {"X-CSRF-Token": _csrf(client) or ""}


def _current_jti(client) -> str:
    sessions = client.get("/api/auth/sessions").json()["sessions"]
    return next(s["jti"] for s in sessions if s["current"])


# ---------------------------------------------------------------------------
# Login / me
# ---------------------------------------------------------------------------


def test_login_success_sets_cookies_and_returns_user(client):
    r = _login(client)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "admin"
    assert body["csrf_token"] == _csrf(client)

    raw = {h.split("=", 1)[0]: h for h in r.headers.get_list("set-cookie")}
    assert "HttpOnly" in raw["ai_research_access"]
    assert "HttpOnly" in raw["ai_research_refresh"]
    assert "Path=/api/auth" in raw["ai_research_refresh"]
    assert "HttpOnly" not in raw["ai_research_csrf"]


@pytest.mark.parametrize("username,password", [
    ("admin", "wrong-password"),
    ("nobody", "whatever-pass"),
    ("ghost", "ghost-pass-123"),
])
def test_login_rejects_bad_credentials(client, username, password):
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_me_requires_authentication(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "not_authenticated"


def test_me_returns_current_user(client):
    _login(client, "editor", "editor-pass-123")
    r = client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["username"] == "editor"
    assert r.json()["role"] == "editor"


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def test_refresh_requires_csrf(client):
    _login(client)
    r = client.post("/api/auth/refresh")
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_refresh_rotates_access_token(client):
    _login(client)
    old_access = client.cookies.get("ai_research_access")
    r = client.post("/api/auth/refresh", headers=_csrf_header(client))
    assert r.status_code == 200
    new_access = client.cookies.get("ai_research_access")
    assert new_access and new_access != old_access
    assert r.json()["csrf_token"] == _csrf(client)


def test_refresh_rejects_non_refresh_token(client):
    _login(client)
    access = client.cookies.get("ai_research_access")
    client.cookies.clear()
    client.cookies.set("ai_research_refresh", access, path="/api/auth")
    client.cookies.set("ai_research_csrf", "csrf-value", path="/")
    r = client.post("/api/auth/refresh", headers={"X-CSRF-Token": "csrf-value"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_refresh"


def test_refresh_rejects_revoked_session(client):
    from web.auth import db as user_db
    from web.auth import jwt as jwt_helper

    _login(client)
    payload = jwt_helper.decode_token(client.cookies.get("ai_research_refresh"))
    user_db.revoke_session(payload["jti"])
    r = client.post("/api/auth/refresh", headers=_csrf_header(client))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "revoked"


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------


def test_logout_requires_csrf_even_after_clearing_cookies(client):
    _login(client)
    r = client.post("/api/auth/logout")
    assert r.status_code == 403
    after = client.get("/api/auth/me")
    assert after.status_code == 401
    assert after.json()["error"]["code"] == "not_authenticated"


def test_logout_revokes_access_token(client, app):
    _login(client)
    access = client.cookies.get("ai_research_access")
    r = client.post("/api/auth/logout", headers=_csrf_header(client))
    assert r.status_code == 200

    other = TestClient(app)
    other.cookies.set("ai_research_access", access, path="/")
    r2 = other.get("/api/auth/me")
    assert r2.status_code == 401
    assert r2.json()["error"]["code"] == "revoked"


# ---------------------------------------------------------------------------
# RBAC + CSRF on real routes
# ---------------------------------------------------------------------------


def test_viewer_forbidden_from_admin_route(client):
    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/users",
                    json={"username": "x1", "password": "secret-1", "role": "viewer"},
                    headers=_csrf_header(client))
    assert r.status_code == 403
    err = r.json()["error"]
    assert err["code"] == "forbidden"
    assert err["details"]["required"] == "admin"
    assert err["details"]["current_role"] == "viewer"


def test_editor_forbidden_from_admin_route(client):
    _login(client, "editor", "editor-pass-123")
    r = client.post("/api/users",
                    json={"username": "x2", "password": "secret-1", "role": "viewer"},
                    headers=_csrf_header(client))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_unsafe_route_without_csrf_rejected(client):
    _login(client)
    r = client.post("/api/users",
                    json={"username": "sneaky", "password": "sneaky-pass", "role": "admin"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"
    listing = client.get("/api/users").json()
    assert all(u["username"] != "sneaky" for u in listing)


def test_admin_can_create_user_with_csrf(client):
    _login(client)
    r = client.post("/api/users",
                    json={"username": "newbie", "password": "newbie-pass", "role": "editor"},
                    headers=_csrf_header(client))
    assert r.status_code == 201
    assert r.json()["username"] == "newbie"
    assert r.json()["role"] == "editor"

    client.post("/api/auth/logout", headers=_csrf_header(client))
    assert _login(client, "newbie", "newbie-pass").status_code == 200


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


def test_api_token_authenticates_and_bypasses_csrf(client, app):
    _login(client)
    created = client.post("/api/auth/tokens", json={"name": "ci"},
                          headers=_csrf_header(client))
    assert created.status_code == 200
    token = created.json()["token"]
    assert token.startswith("air_")

    api = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    me = api.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"

    r = api.post("/api/users",
                 json={"username": "via-token", "password": "token-pass", "role": "viewer"})
    assert r.status_code == 201


def test_api_token_creation_requires_csrf(client):
    _login(client)
    r = client.post("/api/auth/tokens", json={"name": "nope"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_api_token_revoked_rejected(client, app):
    _login(client)
    created = client.post("/api/auth/tokens", json={"name": "temp"},
                          headers=_csrf_header(client)).json()
    api = TestClient(app, headers={"Authorization": f"Bearer {created['token']}"})
    assert api.get("/api/auth/me").status_code == 200

    r = client.delete(f"/api/auth/tokens/{created['id']}", headers=_csrf_header(client))
    assert r.status_code == 200
    assert api.get("/api/auth/me").status_code == 401


def test_invalid_api_token_rejected(app):
    api = TestClient(app, headers={"Authorization": "Bearer air_deadbeef"})
    assert api.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_sessions_listing_marks_current(client):
    _login(client)
    r = client.get("/api/auth/sessions")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert sum(1 for s in sessions if s["current"]) == 1
    assert {s["kind"] for s in sessions} == {"access", "refresh"}


def test_revoke_current_session_requires_csrf(client):
    _login(client)
    jti = _current_jti(client)
    r = client.delete(f"/api/auth/sessions/{jti}")
    assert r.status_code == 403
    assert client.get("/api/auth/me").status_code == 200


def test_revoke_current_session_clears_cookies(client):
    _login(client)
    jti = _current_jti(client)
    r = client.delete(f"/api/auth/sessions/{jti}", headers=_csrf_header(client))
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 401


# ---------------------------------------------------------------------------
# Change password
# ---------------------------------------------------------------------------


def test_change_password_requires_csrf(client):
    _login(client)
    r = client.post("/api/auth/change-password",
                    json={"current_password": "admin-pass-123",
                          "new_password": "brand-new-pass"})
    assert r.status_code == 403


def test_change_password_wrong_current_password(client):
    _login(client)
    r = client.post("/api/auth/change-password",
                    json={"current_password": "nope", "new_password": "brand-new-pass"},
                    headers=_csrf_header(client))
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


def test_change_password_short_new_password(client):
    _login(client)
    r = client.post("/api/auth/change-password",
                    json={"current_password": "admin-pass-123", "new_password": "short"},
                    headers=_csrf_header(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_password"


def test_change_password_revokes_other_sessions(client, app):
    _login(client)
    other = TestClient(app)
    _login(other)
    assert other.get("/api/auth/me").status_code == 200

    r = client.post("/api/auth/change-password",
                    json={"current_password": "admin-pass-123",
                          "new_password": "rotated-pass-1"},
                    headers=_csrf_header(client))
    assert r.status_code == 200
    assert client.get("/api/auth/me").status_code == 200

    revoked = other.get("/api/auth/me")
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "revoked"

    fresh = TestClient(app)
    assert _login(fresh, password="rotated-pass-1").status_code == 200


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def test_decode_token_rejects_garbage():
    from web.auth import jwt as jwt_helper

    assert jwt_helper.decode_token("not-a-jwt") is None
    assert jwt_helper.decode_token("") is None
