"""Tests for report meta columns (favorite / tags / read) and migration."""

from __future__ import annotations

import sqlite3

import pytest

from web.indexer import db as index_db


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(tmp_path / "reports.db"))
    index_db.init_db()
    return tmp_path


def _add(path="a/one.md", title="One"):
    index_db.upsert_report(path, mtime=1000.0, size_bytes=10,
                           job_name="job", title=title, category="cat",
                           content="hello world")


def test_migration_adds_columns_to_old_db(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute(
        "CREATE TABLE reports (path TEXT PRIMARY KEY, job_name TEXT DEFAULT '',"
        " size_bytes INTEGER DEFAULT 0, mtime REAL NOT NULL, title TEXT DEFAULT '',"
        " category TEXT DEFAULT '', indexed_at REAL NOT NULL)")
    conn.execute("INSERT INTO reports VALUES ('x.md','j',1,1,'t','c',1)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(old))
    index_db.init_db()

    cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(reports)")}
    assert {"favorite", "tags", "read_at"} <= cols
    row = index_db.get_report("x.md")
    assert row["favorite"] is False
    assert row["tags"] == []
    assert row["read"] is False


def test_favorite_tags_read_roundtrip(db):
    _add()
    updated = index_db.set_report_meta("a/one.md", favorite=True,
                                       tags=["ai", "pricing", "ai"], read=True)
    assert updated["favorite"] is True
    assert updated["tags"] == ["ai", "pricing"]
    assert updated["read"] is True

    again = index_db.get_report("a/one.md")
    assert again["favorite"] is True and again["tags"] == ["ai", "pricing"]

    index_db.set_report_meta("a/one.md", favorite=False, read=False)
    assert index_db.get_report("a/one.md")["favorite"] is False
    assert index_db.get_report("a/one.md")["read"] is False


def test_set_meta_unknown_path_returns_none(db):
    assert index_db.set_report_meta("nope.md", favorite=True) is None


def test_list_filters(db):
    _add("a/one.md")
    _add("b/two.md")
    index_db.set_report_meta("a/one.md", favorite=True, tags=["ai"])
    index_db.set_report_meta("b/two.md", tags=["infra"], read=True)

    favs = index_db.list_reports(favorite=True)
    assert [i["path"] for i in favs["items"]] == ["a/one.md"]

    tagged = index_db.list_reports(tag="infra")
    assert [i["path"] for i in tagged["items"]] == ["b/two.md"]

    unread = index_db.list_reports(unread=True)
    assert [i["path"] for i in unread["items"]] == ["a/one.md"]


def test_search_includes_meta(db):
    _add()
    index_db.set_report_meta("a/one.md", favorite=True, tags=["ai"])
    results = index_db.search_reports("hello")
    assert results and results[0]["favorite"] is True
    assert results[0]["tags"] == ["ai"]


def test_search_sanitizes_fts_operators(db):
    _add()
    assert index_db.search_reports("hello") != []
    assert index_db.search_reports("zzz-nothing") == []
    assert index_db.search_reports("hello*") != []
    assert index_db.search_reports('"unbalanced') == []
    assert index_db.search_reports("") == []
    assert index_db.search_reports("  ") == []


def test_list_tags_with_counts(db):
    _add("a/one.md")
    _add("b/two.md")
    index_db.set_report_meta("a/one.md", tags=["ai", "pricing"])
    index_db.set_report_meta("b/two.md", tags=["ai"])

    tags = {t["tag"]: t["count"] for t in index_db.list_tags()}
    assert tags == {"ai": 2, "pricing": 1}
