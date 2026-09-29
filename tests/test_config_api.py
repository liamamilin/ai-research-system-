"""System config API tests: structured read + patch merge."""

from __future__ import annotations

import pytest


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


SAMPLE = """# System configuration
ai:
  model: "deepseek-v4.1-flash"   # keep this comment
  base_url: "https://opencode.ai/zen/go/v1"
  api_key_env: "LLM_API_KEY"
search:
  provider: "parallel"
  max_results: 10
budget:
  monthly_usd_limit: 0
"""


@pytest.fixture()
def cfg(web_env):
    tmp_path, _ = web_env
    path = tmp_path / "config" / "system.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_get_requires_admin(client, cfg):
    assert client.get("/api/config/system").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/config/system").status_code == 403


def test_get_returns_parsed(client, cfg):
    _login(client)
    r = client.get("/api/config/system")
    assert r.status_code == 200
    body = r.json()
    assert body["parsed"]["ai"]["model"] == "deepseek-v4.1-flash"
    assert body["parsed"]["search"]["max_results"] == 10
    assert "content" in body and "masked" in body


def test_patch_merges_and_preserves_comments(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"model": "new-model"},
                                   "budget": {"monthly_usd_limit": 20}}},
                   headers=_csrf(client))
    assert r.status_code == 200

    text = cfg.read_text(encoding="utf-8")
    assert "keep this comment" in text
    assert 'model: "new-model"' in text
    assert "monthly_usd_limit: 20" in text
    assert 'provider: "parallel"' in text

    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["ai"]["model"] == "new-model"
    assert parsed["budget"]["monthly_usd_limit"] == 20


def test_patch_creates_nested_keys(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"patch": {"notifications": {
                       "enabled": True,
                       "digest": {"enabled": True, "max_actions": 3}}}},
                   headers=_csrf(client))
    assert r.status_code == 200
    parsed = client.get("/api/config/system").json()["parsed"]
    assert parsed["notifications"]["digest"]["max_actions"] == 3


def test_patch_none_deletes_key(client, cfg):
    _login(client)
    client.put("/api/config/system",
               json={"patch": {"budget": None}}, headers=_csrf(client))
    parsed = client.get("/api/config/system").json()["parsed"]
    assert "budget" not in parsed


def test_patch_conflict_detection(client, cfg):
    _login(client)
    mtime = client.get("/api/config/system").json()["mtime"]
    cfg.write_text(SAMPLE + "extra: 1\n", encoding="utf-8")
    import os
    os.utime(cfg, (mtime + 5, mtime + 5))

    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"model": "x"}}, "expected_mtime": mtime},
                   headers=_csrf(client))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


def test_patch_requires_csrf(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"patch": {"ai": {"model": "x"}}})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_invalid_patch_rejected(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"patch": "nope"},
                   headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_patch"


def test_backup_written_on_change(client, cfg, web_env):
    _login(client)
    client.put("/api/config/system",
               json={"patch": {"ai": {"model": "changed"}}}, headers=_csrf(client))
    backups = list((web_env[0] / "state" / "backups").glob("system_*.yaml"))
    assert backups, "expected a backup of system.yaml"


def test_a_malformed_mtime_is_an_error_not_a_disabled_check(client, cfg):
    """The conflict guard exists to stop a concurrent edit being clobbered.

    An `expected_mtime` that could not be parsed raised inside the check, and a
    bare `except (TypeError, ValueError): pass` swallowed it -- so the write
    went ahead with the guard silently off. Leaving the field out is a client
    choosing not to check, which is fine; sending a broken one is a bug and is
    now reported as one.
    """
    _login(client)
    r = client.put("/api/config/system",
                   json={"content": SAMPLE, "expected_mtime": "not-a-timestamp"},
                   headers=_csrf(client))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_mtime", r.text
    assert "keep this comment" in cfg.read_text(encoding="utf-8"), \
        "the file was overwritten despite the malformed guard value"


def test_omitting_the_mtime_still_saves(client, cfg):
    _login(client)
    r = client.put("/api/config/system", json={"content": SAMPLE},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text


def test_a_stale_mtime_is_still_a_conflict(client, cfg):
    _login(client)
    r = client.put("/api/config/system",
                   json={"content": SAMPLE, "expected_mtime": 1.0},
                   headers=_csrf(client))
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "conflict"
    assert "keep this comment" in cfg.read_text(encoding="utf-8")
