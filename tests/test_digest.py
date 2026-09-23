"""Tests for round digests and their notification hook."""

from __future__ import annotations

import json

from core import digest
from core import notify as notify_mod


def _round_dir(tmp_path, date="2026-01-02", actions=None, watch=None, urls=None):
    round_dir = tmp_path / "practical_ai_intelligence" / date
    round_dir.mkdir(parents=True)
    (round_dir / "action_items.json").write_text(json.dumps({
        "actions": actions if actions is not None else [
            {"action": "P1 行动", "priority": "P1"},
            {"action": "P0 紧急行动", "priority": "**P0**"},
        ],
        "tests": [{"test": "T1", "priority": "P0"}],
    }), encoding="utf-8")
    (round_dir / "watchlist.json").write_text(json.dumps({
        "items": watch if watch is not None else [{"topic": "观察A"}, {"topic": "观察B"}],
    }), encoding="utf-8")
    (round_dir / "sources.json").write_text(json.dumps({
        "sources": [{"url": u} for u in (urls or ["https://a.test/1"])],
    }), encoding="utf-8")
    return str(round_dir)


def test_build_digest_orders_actions_by_priority(tmp_path):
    round_dir = _round_dir(tmp_path)
    result = digest.build_digest("2026-01-02", round_dir,
                                 results={"s1": "success", "s2": "failed"})
    assert result["title"] == "情报轮次 2026-01-02"
    text = result["text"]
    assert "完成 1/2" in text and "s2" in text
    assert text.index("P0 紧急行动") < text.index("P1 行动")
    assert "本周测试 1 项" in text
    assert "观察清单 2 条" in text
    assert "来源 1 个" in text


def test_build_digest_caps_actions(tmp_path):
    actions = [{"action": f"行动{i}", "priority": f"P{i}"} for i in range(8)]
    round_dir = _round_dir(tmp_path, actions=actions)
    result = digest.build_digest("2026-01-02", round_dir, max_actions=3)
    numbered = [line for line in result["text"].splitlines()
                if line[:2] in ("1.", "2.", "3.", "4.")]
    assert len(numbered) == 3
    assert "行动0" in result["text"] and "行动3" not in result["text"]


def test_build_digest_clips_long_text(tmp_path):
    round_dir = _round_dir(tmp_path, actions=[{"action": "长" * 300, "priority": "P0"}])
    result = digest.build_digest("2026-01-02", round_dir)
    assert "…" in result["text"]
    assert max(len(line) for line in result["text"].splitlines()) <= 140


def test_build_digest_missing_round(tmp_path):
    assert digest.build_digest("2026-01-02", str(tmp_path / "nope")) is None


def test_digest_for_round_resolves_dir(tmp_path):
    _round_dir(tmp_path)
    result = digest.digest_for_round("2026-01-02", str(tmp_path))
    assert result is not None and "Top 行动" in result["text"]


def test_notify_round_uses_digest(tmp_path, monkeypatch):
    from web.runner import pipeline

    round_dir = _round_dir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "system.yaml").write_text(
        f"defaults:\n  output_dir: \"{tmp_path}\"\n"
        "notifications:\n  digest:\n    enabled: true\n    max_actions: 2\n",
        encoding="utf-8")

    captured = {}
    monkeypatch.setattr(notify_mod, "send",
                        lambda event, title, message, **kw: captured.update(
                            {"event": event, "title": title, "message": message}) or True)

    state = {"date": "2026-01-02", "status": "partial", "cancel_requested": False}
    pipeline._notify_round(state, {"s1": "success", "s2": "failed"},
                           str(tmp_path / "config"))
    assert captured["event"] == "round_finished"
    assert "Top 行动" in captured["message"]
    assert "s2" in captured["message"]


def test_notify_round_falls_back_without_digest(tmp_path, monkeypatch):
    from web.runner import pipeline

    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "system.yaml").write_text(
        f"defaults:\n  output_dir: \"{tmp_path}\"\n", encoding="utf-8")

    captured = {}
    monkeypatch.setattr(notify_mod, "send",
                        lambda event, title, message, **kw: captured.update(
                            {"message": message}) or True)

    state = {"date": "2026-01-02", "status": "partial", "cancel_requested": False}
    pipeline._notify_round(state, {"s1": "success", "s2": "failed"},
                           str(tmp_path / "config"))
    assert "完成 1/2" in captured["message"]
    assert "失败: s2" in captured["message"] or "失败：s2" in captured["message"]
