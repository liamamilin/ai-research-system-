"""Calendar contract shared by validation, previews and missed-run checks."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core import cron
from web.services import scheduler


@pytest.mark.parametrize("expression", [
    "60 8 * * *", "0 24 * * *", "0 8 0 * *", "0 8 * 13 *", "0 8 * * 8",
    "*/0 * * * *", "*/61 * * * *", "0 8 * * fri-mon", "0 8 * * mon,,fri",
    "0 8 * * *\n", "0 8 * *", "0 8 31 2 *",
])
def test_invalid_calendars_rejected(expression):
    with pytest.raises(ValueError):
        cron.preview(expression)


def test_next_three_excludes_current_minute():
    data = cron.preview("0 8 * * *", datetime(2026, 9, 25, 8))
    assert data["next_runs"] == [f"2026-09-{d}T08:00:00" for d in (26, 27, 28)]


def test_sunday_alias_and_named_lists():
    for expression in ("0 8 * * 7", "0 8 * * sun"):
        assert cron.preview(expression, datetime(2026, 9, 25))["next_runs"][0] == "2026-09-27T08:00:00"
    assert cron.preview("0 8 * jan,sep mon,wed", datetime(2026, 9, 25))["next_runs"][0] == "2026-09-28T08:00:00"


def test_month_day_and_weekday_use_or():
    entry = cron.parse_schedule("0 8 1 * 1")
    assert cron.fire_times(entry, datetime(2026, 9, 25)) == [
        datetime(2026, 9, 28, 8), datetime(2026, 10, 1, 8), datetime(2026, 10, 5, 8)]
    assert scheduler.last_fire_before(entry, datetime(2026, 10, 1, 9)) == datetime(2026, 10, 1, 8)


def test_sparse_monthly_and_leap_day():
    assert scheduler.last_fire_before(cron.parse_schedule("0 8 1 * *"), datetime(2026, 9, 25)) == datetime(2026, 9, 1, 8)
    assert cron.preview("0 8 29 2 *", datetime(2026, 9, 25))["next_runs"] == [
        "2028-02-29T08:00:00", "2032-02-29T08:00:00"]


def test_dst_gap_and_fold():
    zone = ZoneInfo("America/New_York")
    spring = cron.preview("30 2 * * *", datetime(2026, 3, 8, 0, tzinfo=zone))
    assert spring["next_runs"][0] == "2026-03-09T02:30:00-04:00"
    autumn = cron.preview("30 1 * * *", datetime(2026, 11, 1, 0, tzinfo=zone))
    assert autumn["next_runs"][:2] == ["2026-11-01T01:30:00-04:00", "2026-11-01T01:30:00-05:00"]
