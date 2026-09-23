"""Reports API tests: meta updates, filters, tags."""

from __future__ import annotations

import pytest

from web.indexer import db as index_db


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


@pytest.fixture()
def reports(web_env):
    tmp_path, _ = web_env
    index_db.init_db()
    for rel, title, content in (("a/one.md", "One", "hello"),
                                ("b/two.md", "Two", "world")):
        path = tmp_path / "output" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        index_db.upsert_report(rel, mtime=2000.0, title=title,
                               job_name=f"job_{rel[0]}", category="cat",
                               content=content)
    return tmp_path


def test_meta_requires_auth(client, reports):
    assert client.patch("/api/reports/meta", json={"path": "a/one.md"}).status_code == 401


def test_meta_requires_editor(client, reports):
    _login(client, "viewer", "viewer-pass-123")
    r = client.patch("/api/reports/meta",
                     json={"path": "a/one.md", "favorite": True},
                     headers=_csrf(client))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"


def test_meta_requires_csrf(client, reports):
    _login(client)
    r = client.patch("/api/reports/meta",
                     json={"path": "a/one.md", "favorite": True})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "csrf_failed"


def test_meta_update_and_filters(client, reports):
    _login(client)
    r = client.patch("/api/reports/meta",
                     json={"path": "a/one.md", "favorite": True,
                           "tags": ["ai", "ai", " pricing "], "read": True},
                     headers=_csrf(client))
    assert r.status_code == 200
    body = r.json()
    assert body["favorite"] is True
    assert body["tags"] == ["ai", "pricing"]
    assert body["read"] is True

    favs = client.get("/api/reports", params={"favorite": "true"}).json()
    assert [i["path"] for i in favs["items"]] == ["a/one.md"]

    unread = client.get("/api/reports", params={"unread": "true"}).json()
    assert [i["path"] for i in unread["items"]] == ["b/two.md"]

    tagged = client.get("/api/reports", params={"tag": "pricing"}).json()
    assert [i["path"] for i in tagged["items"]] == ["a/one.md"]

    tags = client.get("/api/reports/tags").json()["tags"]
    assert {t["tag"] for t in tags} == {"ai", "pricing"}


def test_get_meta_endpoint(client, reports):
    _login(client, "viewer", "viewer-pass-123")
    r = client.get("/api/reports/meta", params={"path": "a/one.md"})
    assert r.status_code == 200
    assert r.json()["path"] == "a/one.md"
    assert r.json()["favorite"] is False

    assert client.get("/api/reports/meta", params={"path": "nope.md"}).status_code == 404


def test_raw_includes_index_meta(client, reports):
    _login(client)
    r = client.get("/api/reports/raw", params={"path": "a/one.md"})
    assert r.status_code == 200
    assert r.json()["index"]["path"] == "a/one.md"


def test_rating_update_and_summary(client, reports):
    _login(client)
    r = client.patch("/api/reports/meta",
                     json={"path": "a/one.md", "rating": 5, "rating_note": "很好"},
                     headers=_csrf(client))
    assert r.status_code == 200
    assert r.json()["rating"] == 5
    assert r.json()["rating_note"] == "很好"

    r2 = client.patch("/api/reports/meta",
                      json={"path": "b/two.md", "rating": 3},
                      headers=_csrf(client))
    assert r2.status_code == 200

    summary = client.get("/api/usage/ratings").json()
    assert summary["count"] == 2
    assert summary["average"] == 4.0
    assert {j["job_name"] for j in summary["per_job"]} == {"job_a", "job_b"}


def test_rating_validation(client, reports):
    _login(client)
    for bad in (6, -1, "5", True):
        r = client.patch("/api/reports/meta",
                         json={"path": "a/one.md", "rating": bad},
                         headers=_csrf(client))
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "invalid_rating"


def test_meta_errors(client, reports):
    _login(client)
    r = client.patch("/api/reports/meta", json={"path": "nope.md"},
                     headers=_csrf(client))
    assert r.status_code == 404

    r2 = client.patch("/api/reports/meta", json={"path": "a/one.md", "tags": "ai"},
                      headers=_csrf(client))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_tags"

    r3 = client.patch("/api/reports/meta", json={"favorite": True},
                      headers=_csrf(client))
    assert r3.status_code == 422

    r4 = client.patch("/api/reports/meta", json={"path": "a/one.md", "read": "yes"},
                      headers=_csrf(client))
    assert r4.status_code == 422
