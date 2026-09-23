"""Tracking API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import tracking


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


@pytest.fixture()
def seeded(web_env):
    tmp_path, _ = web_env
    tracking.use_state_dir(str(tmp_path / "state"))
    tracking.sync_round(
        "2026-01-02",
        [{"action": "迁移模型", "priority": "P0"}, {"action": "加引用闸门", "priority": "P1"}],
        [{"test": "T1 对照", "priority": "P0"}],
        [{"topic": "GPT-5.5 退役"}],
    )
    return tmp_path


def test_list_actions_requires_auth(client):
    assert client.get("/api/pipeline/actions").status_code == 401


def test_list_actions_with_carry_over(client, seeded):
    _login(client, "viewer", "viewer-pass-123")
    r = client.get("/api/pipeline/actions")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 4
    assert body["carry_over"]["date"] == "2026-01-02"
    assert len(body["carry_over"]["new"]) == 4


def test_list_actions_filters_by_kind_and_status(client, seeded):
    _login(client)
    r = client.get("/api/pipeline/actions", params={"kind": "action"})
    assert r.status_code == 200
    assert {i["kind"] for i in r.json()["items"]} == {"action"}

    r2 = client.get("/api/pipeline/actions", params={"status": "done"})
    assert r2.json()["items"] == []

    r3 = client.get("/api/pipeline/actions", params={"kind": "nope"})
    assert r3.status_code == 422


def test_viewer_cannot_update(client, seeded):
    _login(client, "viewer", "viewer-pass-123")
    item_id = client.get("/api/pipeline/actions").json()["items"][0]["id"]
    r = client.patch(f"/api/pipeline/actions/{item_id}", json={"status": "done"},
                     headers=_csrf(client))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_update_requires_csrf(client, seeded):
    _login(client)
    item_id = client.get("/api/pipeline/actions").json()["items"][0]["id"]
    r = client.patch(f"/api/pipeline/actions/{item_id}", json={"status": "done"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_update_status_and_note(client, seeded):
    _login(client)
    item_id = client.get("/api/pipeline/actions").json()["items"][0]["id"]
    r = client.patch(f"/api/pipeline/actions/{item_id}",
                     json={"status": "done", "note": "已上线"},
                     headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert r.json()["note"] == "已上线"

    done = client.get("/api/pipeline/actions", params={"status": "done"}).json()["items"]
    assert [i["id"] for i in done] == [item_id]


def test_update_unknown_item_and_bad_status(client, seeded):
    _login(client)
    r = client.patch("/api/pipeline/actions/deadbeef", json={"status": "done"},
                     headers=_csrf(client))
    assert r.status_code == 404

    item_id = client.get("/api/pipeline/actions").json()["items"][0]["id"]
    r2 = client.patch(f"/api/pipeline/actions/{item_id}", json={"status": "finished"},
                      headers=_csrf(client))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_status"

    r3 = client.patch(f"/api/pipeline/actions/{item_id}", json={},
                      headers=_csrf(client))
    assert r3.status_code == 422


def test_editor_can_update(client, seeded):
    _login(client, "editor", "editor-pass-123")
    item_id = client.get("/api/pipeline/actions").json()["items"][0]["id"]
    r = client.patch(f"/api/pipeline/actions/{item_id}", json={"status": "dropped"},
                     headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["status"] == "dropped"
