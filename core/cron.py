"""Five-field cron validation and calendar previews, without executing jobs."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTH_NAMES = {name: i + 1 for i, name in enumerate(
    "jan feb mar apr may jun jul aug sep oct nov dec".split())}
DOW_NAMES = {name: i for i, name in enumerate("sun mon tue wed thu fri sat".split())}
KEYS = ("minute", "hour", "day_of_month", "month", "day_of_week")
SPECS = ((0, 59, None), (0, 23, None), (1, 31, None),
         (1, 12, MONTH_NAMES), (0, 7, DOW_NAMES))


def local_now() -> datetime:
    """Use the host's timezone rules, including future DST transitions."""
    try:
        with open("/etc/localtime", "rb") as fh:
            zone = ZoneInfo.from_file(fh, key="系统时区")
        return datetime.now(zone)
    except (OSError, ValueError):
        return datetime.now().astimezone()


def timezone_label() -> str:
    """A readable host timezone; schedules follow the scheduler host."""
    target = os.path.realpath("/etc/localtime")
    name = target.split("zoneinfo/", 1)[-1] if "zoneinfo/" in target else "系统本地时区"
    return f"{name} (UTC{local_now().strftime('%z')})"


def field_values(field: str, low: int, high: int, names: dict | None = None) -> set[int]:
    """Expand one cron field, rejecting malformed or out-of-range values."""
    def atom(value: str) -> int:
        if names and value.lower() in names:
            return names[value.lower()]
        if not value.isascii() or not value.isdigit():
            raise ValueError(f"无效字段：{field}")
        return int(value)

    values: set[int] = set()
    for item in field.split(","):
        base, sep, step_text = item.partition("/")
        step = atom(step_text) if sep else 1
        if step < 1 or step > high - low + 1:
            raise ValueError(f"步长超出范围：{field}")
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = atom(first), atom(last)
        else:
            start = atom(base)
            end = high if sep else start
        if not low <= start <= end <= high:
            raise ValueError(f"字段 {field} 超出范围 {low}–{high}")
        values.update(range(start, end + 1, step))
    return values


def parse_schedule(schedule: str) -> dict:
    """Validate and normalize a standard five-field cron expression."""
    if any(c in schedule for c in "\r\n\x00"):
        raise ValueError("执行时间不能包含换行")
    fields = schedule.split()
    if len(fields) != 5:
        raise ValueError("执行时间必须为 5 字段 cron 表达式")
    for field, spec in zip(fields, SPECS):
        field_values(field, *spec)
    return dict(zip(KEYS, fields))


def _calendar_matches(entry: dict, values: list[set[int]], day: datetime) -> bool:
    dom = day.day in values[2]
    dow = (day.weekday() + 1) % 7
    weekday = dow in values[4] or (dow == 0 and 7 in values[4])
    # Vixie cron: restricted DOM and DOW are OR; a leading wildcard uses AND.
    day_ok = (dom and weekday) if (entry["day_of_month"].startswith("*")
                                  or entry["day_of_week"].startswith("*")) else (dom or weekday)
    return day.month in values[3] and day_ok


def fire_times(entry: dict, when: datetime, *, count: int = 3,
               backwards: bool = False, days: int = 366 * 8) -> list[datetime]:
    """Find calendar slots by day, avoiding minute scans for sparse schedules.

    Forward previews exclude the current instant. Backward searches include
    the current minute. Both use the same cron day-of-week semantics.
    """
    values = [field_values(str(entry[key]), *spec) for key, spec in zip(KEYS, SPECS)]
    boundary = when.replace(second=0, microsecond=0) if backwards else when
    def stamp(dt):
        return dt.timestamp() if dt.tzinfo else dt
    times: list[datetime] = []
    for offset in range(days + 1):
        day = when + timedelta(days=-offset if backwards else offset)
        if not _calendar_matches(entry, values, day):
            continue
        candidates = []
        for hour in sorted(values[1]):
            for minute in sorted(values[0]):
                candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
                if candidate.tzinfo:
                    # Drop nonexistent wall times; include both folds when DST ends.
                    for fold in (0, 1):
                        folded = candidate.replace(fold=fold)
                        actual = folded.astimezone(timezone.utc).astimezone(folded.tzinfo)
                        if actual == folded and actual.fold == fold:
                            candidates.append(folded)
                else:
                    candidates.append(candidate)
        for candidate in sorted(candidates, key=stamp, reverse=backwards):
            eligible = stamp(candidate) <= stamp(boundary) if backwards else stamp(candidate) > stamp(boundary)
            if eligible:
                times.append(candidate)
                if len(times) >= count:
                    return times
    return times


def preview(schedule: str, now: datetime | None = None) -> dict:
    """Return the next three executions or reject an impossible calendar."""
    entry = parse_schedule(schedule)
    upcoming = fire_times(entry, now or local_now())
    if not upcoming:
        raise ValueError("未来 8 年没有可执行日期，请检查月份与日期组合")
    return {"schedule": " ".join(entry.values()), "timezone": timezone_label(),
            "next_runs": [dt.isoformat(timespec="seconds") for dt in upcoming]}
