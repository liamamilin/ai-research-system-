"""Tests for CLI round-finish hooks (artifacts + tracking + digest)."""

from __future__ import annotations

import json

from core import notify as notify_mod
from core import tracking
from core.round_finish import finish_round

P9 = """# Executive Synthesis

## 2. 立即行动（Immediate Actions）

| 行动 | 优先级 |
|---|---|
| 迁移模型 | P0 |

## 9. 观察清单（Watchlist）

| 议题 | 观察点 |
|---|---|
| GPT-5.5 退役 | 是否强制切换 |
"""


def _make_round(tmp_path, date="2026-01-02"):
    round_dir = tmp_path / "output" / "practical_ai_intelligence" / date
    round_dir.mkdir(parents=True)
    (round_dir / "09_executive_synthesis_and_actions.md").write_text(
        P9, encoding="utf-8")
    return round_dir


def test_finish_round_exports_and_syncs(tmp_path, monkeypatch):
    _make_round(tmp_path)
    monkeypatch.setattr(tracking, "_DB_PATH_OVERRIDE",
                        str(tmp_path / "state" / "tracking.db"))

    summary = finish_round("2026-01-02", output_dir=str(tmp_path / "output"),
                           state_dir=str(tmp_path / "state"),
                           config_dir=str(tmp_path / "config"),
                           notify=False)
    assert summary["artifacts"] is True
    assert summary["tracking"]["new"] == 2
    assert summary["tracking"]["updated"] == 0
    assert summary["warnings"] == []

    round_dir = tmp_path / "output" / "practical_ai_intelligence" / "2026-01-02"
    assert (round_dir / "action_items.json").is_file()
    assert (round_dir / "sources.json").is_file()

    items = tracking.list_items()
    assert {i["kind"] for i in items} == {"action", "watch"}


def test_finish_round_sends_digest(tmp_path, monkeypatch):
    _make_round(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "system.yaml").write_text(
        "notifications:\n  enabled: true\n  webhook_url: \"http://hook.test/x\"\n",
        encoding="utf-8")

    captured = {}
    monkeypatch.setattr(notify_mod, "send",
                        lambda event, title, message, **kw: captured.update(
                            {"event": event, "title": title, "message": message}) or True)

    summary = finish_round("2026-01-02", output_dir=str(tmp_path / "output"),
                           state_dir=str(tmp_path / "state"),
                           config_dir=str(tmp_path / "config"))
    assert summary["notified"] is True
    assert captured["event"] == "round_finished"
    assert "迁移模型" in captured["message"]


def test_finish_round_missing_documents(tmp_path, monkeypatch):
    summary = finish_round("2026-01-02", output_dir=str(tmp_path / "output"),
                           state_dir=str(tmp_path / "state"),
                           config_dir=str(tmp_path / "config"),
                           notify=False)
    assert summary["artifacts"] is False
    assert summary["tracking"] is None


def test_finish_round_notification_failure_is_silent(tmp_path, monkeypatch):
    _make_round(tmp_path)

    def boom(*args, **kwargs):
        raise OSError("webhook down")

    monkeypatch.setattr(notify_mod, "send", boom)
    summary = finish_round("2026-01-02", output_dir=str(tmp_path / "output"),
                           state_dir=str(tmp_path / "state"),
                           config_dir=str(tmp_path / "config"))
    assert summary["artifacts"] is True
    assert summary["notified"] is False
