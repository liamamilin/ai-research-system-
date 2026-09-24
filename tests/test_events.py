"""Cross-round event memory: registration, dedup and prompt suppression."""

from __future__ import annotations

import os

import pytest

from core import events


@pytest.fixture()
def events_db(tmp_path, monkeypatch):
    # Same location the engine uses: <workspace>/state/events.db
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setattr(events, "_DB_PATH_OVERRIDE", str(state / "events.db"))
    events.init_db()
    yield
    events.set_db_path(None)


REPORT = """| 发现 | 影响 | 证据来源 |
| --- | --- | --- |
| Gemini 3.8 Flash 降价 | 成本下降 | https://ai.google.dev/pricing |
| Claude Opus 5.5 发布 | 能力提升 | https://www.anthropic.com/news/opus-5-5 |
"""


def test_register_report_records_urls(events_db):
    result = events.register_report("daily", "2026-09-24", REPORT)
    assert result == {"new": 2, "repeat": 0, "total": 2}
    stored = events.recent_events(days=3650)
    assert {e["host"] for e in stored} == {"ai.google.dev", "anthropic.com"}


def test_reregistering_same_report_is_not_a_repeat(events_db):
    events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    again = events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    assert again["new"] == 0
    assert again["repeat"] == 0
    assert events.stats()["total"] == 2


def test_next_round_counts_repeats(events_db):
    events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    result = events.register_report("daily", "2026-09-25", REPORT, round_date="2026-09-25")
    assert result["new"] == 0
    assert result["repeat"] == 2
    assert events.round_counts("2026-09-25") == {"total": 2, "new": 0, "repeat": 2}
    assert events.round_counts("2026-09-24")["new"] == 2


def test_times_seen_increments_across_rounds(events_db):
    events.register_report("a", "2026-09-24", REPORT, round_date="2026-09-24")
    events.register_report("b", "2026-09-25", REPORT, round_date="2026-09-25")
    row = [e for e in events.recent_events(days=3650) if e["host"] == "ai.google.dev"][0]
    assert row["times_seen"] == 2
    assert row["first_seen"] == "2026-09-24"
    assert row["last_seen"] == "2026-09-25"
    assert set(row["rounds"].split(",")) == {"2026-09-24", "2026-09-25"}


def test_normalized_urls_merge_tracking_variants(events_db):
    text = "| 发现 | 来源 |\n| --- | --- |\n| x | https://WWW.Example.com/a/?utm_source=x#frag |"
    events.register_report("d", "2026-09-24", text, round_date="2026-09-24")
    text2 = "| 发现 | 来源 |\n| --- | --- |\n| x | https://example.com/a |"
    result = events.register_report("d", "2026-09-25", text2, round_date="2026-09-25")
    assert result["repeat"] == 1
    assert events.stats()["total"] == 1


def test_context_label_prefers_row_label(events_db):
    events.register_report("d", "2026-09-24", REPORT, round_date="2026-09-24")
    row = [e for e in events.recent_events(days=3650) if e["host"] == "ai.google.dev"][0]
    assert "Gemini 3.8 Flash" in row["context"]


def test_context_drops_generic_source_labels(events_db):
    text = "| 结论 | 来源 |\n| --- | --- |\n| 定价下调 | https://example.com/p |"
    events.register_report("d", "2026-09-24", text, round_date="2026-09-24")
    row = events.recent_events(days=3650)[0]
    assert row["context"] == "定价下调"


def test_reported_block_is_prompt_ready(events_db):
    assert events.reported_block() == ""
    events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    block = events.reported_block(days=7, limit=5)
    assert "已报道" in block
    assert "ai.google.dev" in block
    assert "Gemini 3.8 Flash" in block
    assert len(block.splitlines()) == 3  # header + 2 sources


def test_reported_block_respects_limit(events_db):
    text = "\n".join(
        f"| f{i} | https://site{i}.test/x |" for i in range(10)
    )
    events.register_report("d", "2026-09-24", text, round_date="2026-09-24")
    block = events.reported_block(days=7, limit=3)
    assert len(block.splitlines()) == 4


def test_recent_events_window(events_db):
    events.register_report("d", "2020-01-01", REPORT, round_date="2020-01-01")
    assert events.recent_events(days=7) == []
    assert len(events.recent_events(days=3650)) == 2


def test_register_empty_report(events_db):
    assert events.register_report("d", "2026-09-24", "") == {"new": 0, "repeat": 0, "total": 0}


def test_engine_injects_reported_events(tmp_path, events_db, monkeypatch):
    from core.engine import ResearchEngine

    events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.sys = {}
    engine.workspace_dir = str(tmp_path)
    engine._date_override = None
    prompt = engine._build_prompt({"name": "x", "prompt": "已知：{reported_events}"})
    assert "已报道" in prompt
    assert "ai.google.dev" in prompt
    assert "{" not in prompt


def test_engine_can_disable_event_memory(tmp_path, events_db, monkeypatch):
    from core.engine import ResearchEngine

    events.register_report("daily", "2026-09-24", REPORT, round_date="2026-09-24")
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.sys = {"research": {"event_memory": {"enabled": False}}}
    engine.workspace_dir = str(tmp_path)
    assert engine._reported_events() == ""


def test_round_date_from_output_path():
    from core.engine import ResearchEngine

    path = "output/practical_ai_intelligence/2026-09-24/01_model.md"
    assert ResearchEngine._round_date_from_path(path) == "2026-09-24"
    assert ResearchEngine._round_date_from_path("output/research/2026-09-24_x.md") == "2026-09-24"
    assert ResearchEngine._round_date_from_path("output/simple_test.md") is None


def test_backup_includes_events_db():
    script = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                          "scripts", "backup_state.sh")
    with open(script, encoding="utf-8") as f:
        content = f.read()
    assert '"state/events.db"' in content
