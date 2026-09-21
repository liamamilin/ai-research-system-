"""Regression tests for the cron scheduler parsing/toggling.

Uses a synthetic crontab; the real user crontab is never touched.
"""

from __future__ import annotations

import pytest

from web.services import scheduler as sch


@pytest.fixture
def fake_crontab(monkeypatch):
    store = {
        "lines": [
            "# Practical AI Intelligence - daily 06:00",
            "# cron_id: pipeline",
            "# [PAUSED] 0 6 * * * export PATH=/x; cd /y && bash z.sh",
            "",
            "# cron_id: other_job",
            "30 8 * * * python run.py other",
        ]
    }
    monkeypatch.setattr(sch, "_get_crontab", lambda: list(store["lines"]))
    monkeypatch.setattr(sch, "_set_crontab",
                        lambda lines: store.__setitem__("lines", list(lines)))
    return store


def test_paused_entry_parses_correctly(fake_crontab):
    jobs = {j["id"]: j for j in sch.list_jobs()}
    assert jobs["pipeline"]["minute"] == "0"
    assert jobs["pipeline"]["hour"] == "6"
    assert jobs["pipeline"]["enabled"] is False
    assert jobs["other_job"]["enabled"] is True


def test_enable_strips_paused_marker_and_keeps_other_jobs(fake_crontab):
    assert sch.toggle_job("pipeline", True) is True
    assert fake_crontab["lines"][2] == "0 6 * * * export PATH=/x; cd /y && bash z.sh"
    assert fake_crontab["lines"][5] == "30 8 * * * python run.py other"


def test_disable_and_reenable(fake_crontab):
    sch.toggle_job("pipeline", True)
    assert sch.toggle_job("pipeline", False) is True
    assert fake_crontab["lines"][2].startswith("# 0 6 * * *")
    assert sch.toggle_job("pipeline", True) is True
    assert fake_crontab["lines"][2].startswith("0 6 * * *")


def test_remove_only_target_job(fake_crontab):
    assert sch.remove_job("pipeline") is True
    remaining = "\n".join(fake_crontab["lines"])
    assert "pipeline" not in remaining
    assert "30 8 * * * python run.py other" in remaining


def test_looks_like_cron_rejects_prose():
    # A plain comment with many words must not parse as a cron entry
    assert sch._parse_cron_entry("# Practical AI Intelligence - daily 06:00") is None
