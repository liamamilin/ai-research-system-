"""Same-story clustering for the cross-round event memory."""

import pytest

from core import events


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    monkeypatch.setattr(events, "_DB_PATH_OVERRIDE", str(state / "events.db"))
    events.init_db()
    yield
    events.set_db_path(None)


def _register(job, date, content, round_date=None):
    return events.register_report(job, date, content, round_date=round_date)


def test_same_story_requires_two_distinctive_tokens(isolated_db):
    left = frozenset({"maxkb", "agent", "flaw", "critical"})
    right = frozenset({"maxkb", "agent", "flaw", "critical"})
    generic_only = frozenset({"release", "notes", "index", "page"})

    assert events.same_story(left, right) is True
    # "release notes" vs "changelog" style pages are not the same story
    assert events.same_story(generic_only, frozenset({"release", "notes", "docs"})) is False
    # one shared word is not enough
    assert events.same_story(frozenset({"maxkb", "agent"}), frozenset({"maxkb", "flaw"})) is False
    # same site covering it twice is a different problem
    assert events.same_story(left, right, same_host=True) is False


def test_cross_outlet_coverage_is_folded_into_one_event(isolated_db):
    first = "1. MaxKB AI agent flaw | 一手 | https://cyberpress.org/critical-maxkb-ai-agent-flaw"
    second = "2. MaxKB 漏洞 | 二手 | https://gbhackers.com/critical-maxkb-ai-agent-flaw"

    assert _register("p1", "2026-09-24", first)["new"] == 1
    result = _register("p2", "2026-09-24", second)

    assert result["new"] == 0
    assert result["clustered"] == 1
    assert events.cluster_stats() == {"sources": 2, "events": 1, "clustered_sources": 1}


def test_clustered_source_is_not_reported_as_a_new_event(isolated_db):
    _register("p1", "2026-09-24",
              "x | https://techcrunch.com/2026/09/17/openai-caught-models-leaving-notes")
    _register("p2", "2026-09-25",
              "x | https://androidheadlines.com/2026/09/openai-models-caught-leaving-notes-hiding")

    listed = events.recent_events(days=10, limit=50)
    assert len(listed) == 1
    assert listed[0]["host"] == "techcrunch.com"
    assert listed[0]["also_reported_by"] == ["androidheadlines.com"]
    assert listed[0]["sources"] == 1
    assert len(listed[0]["source_urls"]) == 1


def test_unrelated_stay_separate(isolated_db):
    _register("p1", "2026-09-24", "a | https://techcrunch.com/openai-launches-gpt6")
    _register("p2", "2026-09-24", "b | https://theverge.com/anthropic-ships-opus-55")

    assert events.cluster_stats()["events"] == 2


def test_cluster_window_expires(isolated_db):
    _register("p1", "2026-01-01", "x | https://a.example/maxkb-agent-flaw-critical")
    _register("p2", "2026-09-24", "x | https://b.example/maxkb-agent-flaw-critical")

    # eight months later, the same headline is a fresh development, and the old
    # source must not be folded into it
    assert events.cluster_stats()["events"] == 2
    with events.connect() as conn:
        rows = dict(conn.execute(
            "SELECT url_key, event_key FROM events").fetchall())
    older = "https://a.example/maxkb-agent-flaw-critical"
    newer = "https://b.example/maxkb-agent-flaw-critical"
    assert rows[older] == "" and rows[newer] == ""


def test_title_falls_back_to_slug_when_label_is_unusable(isolated_db):
    assert events._title_label("arXiv", "https://arxiv.org/abs/2609.08149") == ""
    assert events._title_label("（2026-09-10）", "https://x.example/none") == ""
    assert events._title_label("来源", "https://x.example/") == ""
    assert events._title_label("", "https://cyberpress.org/critical-maxkb-ai-agent-flaw") == (
        "critical maxkb ai agent flaw")
    assert events._title_label("1. Indie Games Statistics 2026 | 一手 | x", "https://x.example/a") == (
        "Indie Games Statistics 2026")


def test_round_counts_report_clustered_sources(isolated_db):
    _register("p1", "2026-09-24", "x | https://cyberpress.org/critical-maxkb-ai-agent-flaw",
              round_date="2026-09-24")
    _register("p2", "2026-09-24", "x | https://gbhackers.com/critical-maxkb-ai-agent-flaw",
              round_date="2026-09-24")

    counts = events.round_counts("2026-09-24")
    assert counts["total"] == 2
    assert counts["clustered"] == 1
    assert counts["new"] == 2  # both are new links, one of them a known story


def test_reported_block_mentions_corroborating_sources(isolated_db):
    _register("p1", "2026-09-24", "1. MaxKB AI agent flaw | https://cyberpress.org/critical-maxkb-ai-agent-flaw")
    _register("p2", "2026-09-24", "2. MaxKB 漏洞 | https://gbhackers.com/critical-maxkb-ai-agent-flaw")

    block = events.reported_block(days=10, limit=5)
    assert "gbhackers.com" in block
    assert "另见" in block


def test_migration_is_idempotent_on_legacy_rows(isolated_db):
    """A pre-clustering database gains the columns and is backfilled once."""
    events.init_db()
    with events.connect() as conn:
        conn.execute(
            "INSERT INTO events (url_key, url, host, context, first_seen, last_seen,"
            " times_seen, jobs, rounds) VALUES (?,?,?,?,?,?,1,?,?)",
            ("https://old.example/some-story-about-agents", "https://old.example/some-story-about-agents",
             "old.example", "Story about agents", "2026-09-01", "2026-09-01", "p1", "2026-09-01"),
        )
        # Simulate a pre-migration row that has no derived columns.
        conn.execute("UPDATE events SET tokens = '', title = '', title_key = ''")

    events.init_db()
    events.init_db()  # must not throw or double-cluster
    with events.connect() as conn:
        row = conn.execute(
            "SELECT tokens, title FROM events WHERE url_key = ?",
            ("https://old.example/some-story-about-agents",)).fetchone()
    assert row["tokens"]
    assert row["title"] == "Story about agents"
