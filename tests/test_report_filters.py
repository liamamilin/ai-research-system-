"""Time / round / sort filters for the reports list and the directory tree."""

from __future__ import annotations

import os

import pytest

from web.indexer import db as index_db


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _headers(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


@pytest.fixture()
def reports(web_env, monkeypatch):
    """A library with known report dates, isolated from the real index."""
    tmp_path, _ = web_env
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE",
                        str(tmp_path / "state" / "reports.db"))
    index_db.init_db()
    output = tmp_path / "output"
    for rel, mtime in (
        ("practical_ai_intelligence/2026-09-24/09_executive_synthesis_and_actions.md", 1.0),
        ("practical_ai_intelligence/2026-09-24/01_radar.md", 1.0),
        ("practical_ai_intelligence/2026-09-19/09_executive_synthesis_and_actions.md", 2.0),
        ("research/2026-06-25/deep_dive.md", 3.0),
        ("research/old_report.md", 4.0),
        ("archive/misc.md", 5.0),
    ):
        full = output / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(f"# {rel}\n", encoding="utf-8")
        index_db.upsert_report(rel, mtime=mtime, title=rel.split("/")[-1][:-3],
                               content="body", job_name=rel.split("/")[0],
                               category=rel.split("/")[0])
    return tmp_path


def test_report_date_prefers_the_path_and_falls_back_to_mtime():
    assert index_db.report_date_for("x/2026-09-24/report.md", 1.0) == "2026-09-24"
    import time
    stamp = time.strftime("%Y-%m-%d", time.localtime(1_700_000_000))
    assert index_db.report_date_for("archive/no_date.md", 1_700_000_000) == stamp
    assert index_db.report_date_for("archive/no_date.md", 0) == ""


def test_backfill_fills_report_date_for_existing_rows(web_env, monkeypatch):
    """A row written before the column existed must get one on migration."""
    import sqlite3

    tmp_path, _ = web_env
    db_path = tmp_path / "state" / "reports.db"
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(db_path))
    index_db.init_db()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO reports (path, mtime, report_date, indexed_at) VALUES (?,?,'',0)",
            ("research/2026-01-02/old.md", 1.0))
        conn.commit()
    finally:
        conn.close()

    index_db.init_db()  # migration runs again
    conn = sqlite3.connect(str(db_path))
    try:
        value = conn.execute(
            "SELECT report_date FROM reports WHERE path = ?",
            ("research/2026-01-02/old.md",)).fetchone()[0]
    finally:
        conn.close()
    assert value == "2026-01-02"


def test_since_and_until_bound_the_list(reports):
    assert index_db.list_reports(since="2026-09-19", until="2026-09-24")["total"] == 3
    assert index_db.list_reports(since="2026-09-20")["total"] == 2
    assert index_db.list_reports(until="2026-06-25")["total"] == 3
    assert index_db.list_reports()["total"] == 6


def test_filters_combine_with_category(reports):
    result = index_db.list_reports(since="2026-09-01", category="practical_ai_intelligence")
    assert result["total"] == 3


def test_latest_round_picks_the_newest_round_not_the_newest_day(reports):
    assert index_db.latest_round_date() == "2026-09-24"
    result = index_db.list_reports(latest_round=True)
    assert result["total"] == 2
    paths = {item["path"] for item in result["items"]}
    assert all("2026-09-24" in p for p in paths)


def test_latest_round_ignores_a_day_that_is_not_a_round(web_env, monkeypatch):
    """A single ad-hoc report is not a round."""
    tmp_path, _ = web_env
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE",
                        str(tmp_path / "state" / "reports.db"))
    index_db.init_db()
    index_db.upsert_report("practical_ai_intelligence/2026-01-01/09_synthesis.md",
                           mtime=1.0, title="old round")
    index_db.upsert_report("research/2026-09-24/adhoc.md", mtime=2.0, title="adhoc")
    assert index_db.latest_round_date() == "2026-01-01"
    assert index_db.list_reports(latest_round=True)["total"] == 1


def test_sort_orders(reports):
    recent = [i["path"] for i in index_db.list_reports(sort="recent")["items"]]
    oldest = [i["path"] for i in index_db.list_reports(sort="oldest")["items"]]
    # Ordering is by report date, so both 09-24 reports lead.
    assert all("2026-09-24" in p for p in recent[:2])
    assert all("2026-09-24" not in p for p in oldest[:2])
    assert recent != oldest
    titles = [i["title"] for i in index_db.list_reports(sort="title")["items"]]
    assert titles == sorted(titles, key=str.lower)


def test_tree_is_pruned_and_counted_under_the_same_filters(reports):
    full = index_db.get_report_tree()
    names = {n["name"] for n in full}
    assert "archive" in names and "research" in names

    filtered = index_db.get_report_tree(since="2026-09-01")
    filtered_names = {n["name"] for n in filtered}
    assert filtered_names == {"practical_ai_intelligence"}
    top = filtered[0]
    assert top["count"] == 3
    assert sum(len(node["children"]) for node in filtered) == 2  # two round dirs


def test_tree_and_list_agree_on_the_total(reports):
    for kwargs in ({}, {"since": "2026-09-01"}, {"category": "research"},
                   {"latest_round": True}):
        listed = index_db.list_reports(per_page=100, **kwargs)["total"]
        tree = index_db.get_report_tree(**kwargs)
        counted = sum(node.get("count", 0) for node in tree)
        assert listed == counted, kwargs


# --- HTTP surface -----------------------------------------------------------


def test_api_accepts_filters_and_passes_them_to_both_endpoints(client, reports):
    _login(client)
    listed = client.get("/api/reports?since=2026-09-19&until=2026-09-24",
                        headers=_headers(client))
    assert listed.status_code == 200
    assert listed.json()["total"] == 3

    tree = client.get("/api/reports/tree?since=2026-09-19&until=2026-09-24",
                      headers=_headers(client))
    assert tree.status_code == 200
    assert sum(n.get("count", 0) for n in tree.json()) == 3


def test_api_latest_round_and_sort(client, reports):
    _login(client)
    body = client.get("/api/reports?latest_round=true", headers=_headers(client)).json()
    assert body["total"] == 2
    for order in ("recent", "oldest", "title", "size", "coverage"):
        r = client.get(f"/api/reports?sort={order}", headers=_headers(client))
        assert r.status_code == 200, order
        assert r.json()["total"] == 6


def test_api_rejects_a_malformed_date(client, reports):
    _login(client)
    r = client.get("/api/reports?since=yesterday", headers=_headers(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_date"

    tree = client.get("/api/reports/tree?until=2026-13-45", headers=_headers(client))
    assert tree.status_code == 422


def test_api_rejects_an_unknown_sort(client, reports):
    _login(client)
    r = client.get("/api/reports?sort=chaos", headers=_headers(client))
    assert r.status_code == 422


def test_latest_round_excludes_unrelated_reports_from_the_same_day(web_env, monkeypatch):
    """A round is one directory's output, not every report written that day."""
    tmp_path, _ = web_env
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE",
                        str(tmp_path / "state" / "reports.db"))
    index_db.init_db()
    index_db.upsert_report("practical_ai_intelligence/2026-09-24/09_synthesis.md",
                           mtime=1.0, title="round")
    index_db.upsert_report("practical_ai_intelligence/2026-09-24/01_radar.md",
                           mtime=1.0, title="radar")
    index_db.upsert_report("monitoring/2026-09-24/some_check.md",
                           mtime=1.0, title="monitoring")
    index_db.upsert_report("root_level_report_20260924.md",
                           mtime=1.0, title="root")

    result = index_db.list_reports(latest_round=True)
    assert result["total"] == 2
    assert all(p["path"].startswith("practical_ai_intelligence/2026-09-24/")
               for p in result["items"])
    assert sum(n.get("count", 0) for n in index_db.get_report_tree(latest_round=True)) == 2
