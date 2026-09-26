"""Remember-me on the login form, and the gateway settings endpoint.

The point of "remember me" is that the cookie and the token it carries expire
together, so both are asserted -- a cookie that outlives its own JWT just
produces a 401 at the next refresh.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from web.services import yaml_io


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture()
def admin(client, web_env):
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123'})
    return client, {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}


def _cookie(response, name):
    for cookie in response.headers.get_list("set-cookie"):
        if cookie.startswith(f"{name}="):
            return cookie
    return ""


# --- remember me -------------------------------------------------------------

def test_login_without_remember_keeps_the_default_session(client, web_env):
    response = client.post('/api/auth/login',
                           json={'username': 'admin', 'password': 'admin-pass-123'})
    assert response.status_code == 200
    assert response.headers["X-Session-Days"] == "7"
    assert "Max-Age=604800" in _cookie(response, "ai_research_refresh")


def test_remember_me_extends_the_cookie(client, web_env):
    response = client.post('/api/auth/login', json={
        'username': 'admin', 'password': 'admin-pass-123', 'remember': True})
    assert response.status_code == 200
    assert response.headers["X-Session-Days"] == "30"
    assert "Max-Age=2592000" in _cookie(response, "ai_research_refresh")


def test_remember_me_extends_the_token_too_not_just_the_cookie(client, web_env):
    """A 30-day cookie holding a 7-day token would still 401 on day 8."""
    import jwt as pyjwt

    response = client.post('/api/auth/login', json={
        'username': 'admin', 'password': 'admin-pass-123', 'remember': True})
    token = client.cookies.get("ai_research_refresh")
    claims = pyjwt.decode(token, options={"verify_signature": False})
    ttl_days = (claims["exp"] - claims["iat"]) / 86400
    assert 29 <= ttl_days <= 30, f"refresh token only lives {ttl_days:.1f} days"


def test_plain_login_token_keeps_the_short_lifetime(client, web_env):
    import jwt as pyjwt

    client.post('/api/auth/login',
                json={'username': 'admin', 'password': 'admin-pass-123'})
    claims = pyjwt.decode(client.cookies.get("ai_research_refresh"),
                          options={"verify_signature": False})
    assert 6.9 <= (claims["exp"] - claims["iat"]) / 86400 <= 7.1


def test_remember_me_is_audited(client, web_env, tmp_path):
    from web import audit
    client.post('/api/auth/login', json={
        'username': 'admin', 'password': 'admin-pass-123', 'remember': True})
    entries = audit.tail(20)
    assert any(e.get("action") == "login" and (e.get("details") or {}).get("remember")
               for e in entries)


def test_remember_flag_does_not_change_authentication(client, web_env):
    """It buys longevity, never weaker checks."""
    assert client.post('/api/auth/login', json={
        'username': 'admin', 'password': 'wrong', 'remember': True}).status_code == 401


# --- gateway settings --------------------------------------------------------

def _enable_gateway(tmp_path, monkeypatch, lan=False):
    """Switch the gateway on in the config the app actually loads.

    web_env already points AI_RESEARCH_WEB_CONFIG at a temp file, so this
    rewrites *that* file -- the same one the settings came from and the same one
    the route writes back to.
    """
    config = Path(os.environ["AI_RESEARCH_WEB_CONFIG"])
    config.write_text(config.read_text(encoding="utf-8") + (
        f"\ngateway:\n  enabled: true\n  app_port: {free_port()}\n"
        f"  lan_access: {str(lan).lower()}\n"), encoding="utf-8")
    import web.settings as web_settings
    monkeypatch.setattr(web_settings, "_settings", None)
    return config


def test_gateway_settings_are_readable(admin, web_env, tmp_path, monkeypatch):
    _enable_gateway(tmp_path, monkeypatch)
    client, _headers = admin
    payload = client.get('/api/gateway').json()
    from web.settings import get_settings
    settings = get_settings()
    assert payload["enabled"] is True
    assert payload["lan_access"] is False
    # The app reports the configured public port and sits on a different one.
    assert payload["public_port"] == settings.server.port
    assert payload["app_port"] == settings.gateway.app_port
    assert payload["app_port"] != payload["public_port"]
    # No address is advertised while LAN access is off, so the UI cannot show
    # a URL that would not work.
    assert payload["lan_url"] == ""
    assert payload["local_url"] == f"http://127.0.0.1:{settings.server.port}/"


def test_lan_access_can_be_turned_on_and_off(admin, web_env, tmp_path, monkeypatch):
    config = _enable_gateway(tmp_path, monkeypatch)
    client, headers = admin

    on = client.put('/api/gateway', headers=headers, json={'lan_access': True})
    assert on.status_code == 200, on.text
    body = on.json()
    assert body["lan_access"] is True
    assert body["bind_host"] == "0.0.0.0"
    # The listener cannot rebind itself, so the response must say so.
    assert body["restart_required"] is True and body["restart_hint"]
    assert yaml_io.parse_yaml(config.read_text())["gateway"]["lan_access"] is True

    off = client.put('/api/gateway', headers=headers, json={'lan_access': False})
    assert off.json()["bind_host"] == "127.0.0.1"
    assert off.json()["restart_required"] is True
    assert yaml_io.parse_yaml(config.read_text())["gateway"]["lan_access"] is False


def test_setting_the_same_value_is_not_reported_as_changed(admin, web_env, tmp_path, monkeypatch):
    _enable_gateway(tmp_path, monkeypatch, lan=True)
    client, headers = admin
    body = client.put('/api/gateway', headers=headers, json={'lan_access': True}).json()
    assert body["changed"] is False
    assert body["restart_required"] is False


def test_toggling_lan_is_audited(admin, web_env, tmp_path, monkeypatch):
    from web import audit
    _enable_gateway(tmp_path, monkeypatch)
    client, headers = admin
    client.put('/api/gateway', headers=headers, json={'lan_access': True})
    assert any(e.get("action") == "gateway_update" for e in audit.tail(20))


def test_gateway_settings_require_admin(client, web_env, tmp_path, monkeypatch):
    _enable_gateway(tmp_path, monkeypatch)
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'viewer', 'password': 'viewer-pass-123'})
    headers = {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}
    assert client.get('/api/gateway').status_code == 403
    assert client.put('/api/gateway', headers=headers, json={'lan_access': True}).status_code == 403


def test_gateway_toggle_needs_the_csrf_token(admin, web_env, tmp_path, monkeypatch):
    _enable_gateway(tmp_path, monkeypatch)
    client, _headers = admin
    assert client.put('/api/gateway', json={'lan_access': True}).status_code == 403


def test_enabled_cannot_be_flipped_over_http(admin, web_env, tmp_path, monkeypatch):
    """It moves the app to another port; an API call must not pretend otherwise."""
    _enable_gateway(tmp_path, monkeypatch)
    client, headers = admin
    # Unknown fields are ignored rather than silently honoured.
    response = client.put('/api/gateway', headers=headers,
                          json={'lan_access': False, 'enabled': False})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
