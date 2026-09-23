"""Tests for round item tracking."""

from __future__ import annotations

import pytest

from core import tracking


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(tracking, "_DB_PATH_OVERRIDE", str(tmp_path / "tracking.db"))
    tracking.init_db()
    return tmp_path


ACTIONS = [
    {"action": "1. 迁移模型 gpt-5.6", "priority": "P0"},
    {"action": "加引用闸门", "priority": "P1"},
]
TESTS = [{"test": "T1 换壳对照", "priority": "P0"}]
WATCH = [{"topic": "GPT-5.5 退役"}]


def test_item_id_stable_across_numbering_and_whitespace():
    assert tracking.item_id("action", "1. 迁移模型  gpt-5.6") == \
        tracking.item_id("action", "迁移模型 gpt-5.6")
    assert tracking.item_id("action", "迁移模型") != tracking.item_id("watch", "迁移模型")


def test_sync_round_creates_items(db):
    counts = tracking.sync_round("2026-01-01", ACTIONS, TESTS, WATCH)
    assert counts == {"new": 4, "updated": 0}
    items = tracking.list_items()
    assert {i["kind"] for i in items} == {"action", "test", "watch"}
    first = next(i for i in items if i["kind"] == "action")
    assert first["status"] == "open"
    assert first["first_seen"] == "2026-01-01"
    assert first["priority"] in ("P0", "P1")


def test_sync_same_date_does_not_double_count(db):
    tracking.sync_round("2026-01-01", ACTIONS)
    tracking.sync_round("2026-01-01", ACTIONS)
    item = next(i for i in tracking.list_items(kind="action"))
    assert item["times_seen"] == 1


def test_sync_later_round_marks_continuing(db):
    tracking.sync_round("2026-01-01", ACTIONS)
    tracking.sync_round("2026-01-08", ACTIONS[:1] + [{"action": "新行动", "priority": "P2"}])

    item = next(i for i in tracking.list_items(kind="action") if "迁移模型" in i["text"])
    assert item["first_seen"] == "2026-01-01"
    assert item["last_seen"] == "2026-01-08"
    assert item["times_seen"] == 2


def test_set_status_and_filters(db):
    tracking.sync_round("2026-01-01", ACTIONS)
    item = tracking.list_items(kind="action")[0]
    assert tracking.set_status(item["id"], "done", note="已完成") is True

    done = tracking.list_items(status="done")
    assert len(done) == 1
    assert done[0]["note"] == "已完成"
    open_items = tracking.list_items(status="open")
    assert all(i["id"] != item["id"] for i in open_items)


def test_set_status_rejects_unknown_status(db):
    with pytest.raises(ValueError):
        tracking.set_status("deadbeef", "finished")


def test_set_status_unknown_id_returns_false(db):
    assert tracking.set_status("deadbeef", "done") is False


def test_carry_over_classifies_items(db):
    tracking.sync_round("2026-01-01", ACTIONS)
    tracking.sync_round("2026-01-08", ACTIONS[:1] + [{"action": "新行动", "priority": "P2"}])

    report = tracking.carry_over()
    assert report["date"] == "2026-01-08"
    assert [i["text"] for i in report["new"]] == ["新行动"]
    assert any("迁移模型" in i["text"] for i in report["continuing"])
    assert any("加引用闸门" in i["text"] for i in report["open_stale"])


def test_carry_over_empty_db(db):
    assert tracking.carry_over() == {
        "date": None, "new": [], "continuing": [], "open_stale": []}


def test_done_items_not_in_open_stale(db):
    tracking.sync_round("2026-01-01", ACTIONS)
    item = next(i for i in tracking.list_items(kind="action"))
    tracking.set_status(item["id"], "done")
    tracking.sync_round("2026-01-08", [{"action": "别的行动"}])

    report = tracking.carry_over()
    assert all(i["id"] != item["id"] for i in report["open_stale"])
