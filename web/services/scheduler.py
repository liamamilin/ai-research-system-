"""Crontab-based scheduler management.

Reads and edits the user's crontab to schedule recurring job runs.
Works on macOS and Linux.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import Optional

_CRON_LINE_RE = re.compile(
    r"^\s*"
    r"(?P<min>\S+)\s+"
    r"(?P<hour>\S+)\s+"
    r"(?P<dom>\S+)\s+"
    r"(?P<month>\S+)\s+"
    r"(?P<dow>\S+)\s+"
    r"(?P<command>.+)$"
)

_CRON_ID_RE = re.compile(r"#\s*cron_id:\s*(\S+)")

# A plausible cron field: *, numbers, lists/ranges/steps, or 3-letter names.
_CRON_FIELD_RE = re.compile(
    r"^(\*|[\d,\-/]+|\*/\d+|[A-Za-z]{3}(-[A-Za-z]{3})?)$"
)

# Manual pause marker used by the pipeline script: "# [PAUSED] 0 6 * * * ..."
_PAUSED_RE = re.compile(r"^\[PAUSED\]\s*", re.IGNORECASE)


def _strip_wrappers(line: str) -> str:
    """Remove leading comment markers and [PAUSED] from a crontab line."""
    stripped = line.strip()
    if stripped.startswith("#"):
        stripped = stripped[1:].strip()
    return _PAUSED_RE.sub("", stripped)


def _run_crontab(args: list[str]) -> str:
    """Run crontab with given arguments and return stdout."""
    try:
        result = subprocess.run(
            ["crontab"] + args,
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"crontab failed: {result.stderr.strip()}")
        return result.stdout
    except FileNotFoundError:
        raise RuntimeError("crontab not found (not available on this system)")
    except subprocess.TimeoutExpired:
        raise RuntimeError("crontab timed out")


def _get_crontab() -> list[str]:
    """Return current crontab lines, or empty list if no crontab."""
    try:
        text = _run_crontab(["-l"])
        return text.splitlines()
    except RuntimeError as e:
        if "no crontab" in str(e).lower():
            return []
        raise


def _set_crontab(lines: list[str]):
    """Write a new crontab from a list of lines."""
    content = "\n".join(lines) + "\n" if lines else ""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".cron") as f:
        f.write(content)
        tmp_path = f.name
    try:
        _run_crontab([tmp_path])
    finally:
        os.unlink(tmp_path)


def _parse_cron_entry(line: str) -> Optional[dict]:
    """Parse a cron line into a structured entry.

    Handles active lines, commented lines ("# 0 6 * * * ...") and manually
    paused lines ("# [PAUSED] 0 6 * * * ..."). Returns None when the line
    does not look like a cron entry.
    """
    stripped = _strip_wrappers(line)

    m = _CRON_LINE_RE.match(stripped)
    if not m:
        return None

    raw = m.groupdict()
    fields = [raw["min"], raw["hour"], raw["dom"], raw["month"], raw["dow"]]
    if not all(_CRON_FIELD_RE.match(f) for f in fields):
        return None

    return {
        "minute": raw["min"],
        "hour": raw["hour"],
        "day_of_month": raw["dom"],
        "month": raw["month"],
        "day_of_week": raw["dow"],
        "command": raw["command"],
    }


def list_jobs() -> list[dict]:
    """List all cron entries that have a # cron_id: <id> header."""
    lines = _get_crontab()
    result: list[dict] = []
    current_id: Optional[str] = None

    for line in lines:
        id_m = _CRON_ID_RE.match(line)
        if id_m:
            current_id = id_m.group(1)
            continue

        entry = _parse_cron_entry(line)
        if entry and current_id:
            entry["id"] = current_id
            entry["enabled"] = not line.strip().startswith("#")
            result.append(entry)
            current_id = None

    return result


def _find_job_lines(lines: list[str], job_id: str) -> list[int]:
    """Return line indices of the header + command for a given job_id.

    The command line is the first following line that parses as a cron entry
    (active, commented or [PAUSED]) before the next cron_id header. Returns
    just the header index when no command line is found.
    """
    for i, line in enumerate(lines):
        m = _CRON_ID_RE.match(line)
        if not (m and m.group(1) == job_id):
            continue
        for j in range(i + 1, len(lines)):
            if _CRON_ID_RE.match(lines[j]):
                break  # next job starts here; entry looks malformed
            if not lines[j].strip():
                continue
            if _parse_cron_entry(lines[j]):
                return [i, j]
        return [i]
    return []


def add_job(
    job_id: str,
    schedule: str,
    command: str,
) -> dict:
    """Add a new cron entry.

    Args:
        job_id: Unique identifier for this schedule.
        schedule: Cron expression (5 fields: min hour dom mon dow).
        command: Shell command to run.

    Returns the created job dict.
    """
    fields = schedule.strip().split()
    if len(fields) != 5:
        raise ValueError("schedule 必须为 5 字段 cron 表达式")

    lines = _get_crontab()
    # Check for duplicate id (exact match)
    for line in lines:
        m = _CRON_ID_RE.match(line)
        if m and m.group(1) == job_id:
            raise ValueError(f"job_id '{job_id}' 已存在")

    lines.append(f"# cron_id: {job_id}")
    lines.append(f"{' '.join(fields)} {command}")
    lines.append("")
    _set_crontab(lines)

    return {
        "id": job_id,
        "minute": fields[0],
        "hour": fields[1],
        "day_of_month": fields[2],
        "month": fields[3],
        "day_of_week": fields[4],
        "command": command,
        "enabled": True,
    }


def remove_job(job_id: str) -> bool:
    """Remove a cron entry by id. Returns True if removed."""
    lines = _get_crontab()
    indices = _find_job_lines(lines, job_id)
    if not indices:
        return False

    # Remove from highest index first
    for idx in sorted(indices, reverse=True):
        lines.pop(idx)

    _set_crontab(lines)
    return True


def toggle_job(job_id: str, enabled: bool) -> bool:
    """Enable or disable a scheduled job by commenting/uncommenting its command line.

    Handles both plain comments ("# 0 6 ...") and manual [PAUSED] markers
    ("# [PAUSED] 0 6 ..."). Never touches other jobs' lines.
    """
    lines = _get_crontab()
    indices = _find_job_lines(lines, job_id)
    if len(indices) < 2:
        return False

    cmd_idx = indices[-1]
    cmd_line = lines[cmd_idx]
    stripped = cmd_line.strip()
    is_commented = stripped.startswith("#")

    if enabled and is_commented:
        # Remove the comment marker and any [PAUSED] marker, preserving fields
        lines[cmd_idx] = _strip_wrappers(cmd_line)
        _set_crontab(lines)
        return True
    if not enabled and not is_commented:
        # Comment out the line, preserving cron fields
        lines[cmd_idx] = "# " + cmd_line.strip()
        _set_crontab(lines)
        return True

    return False  # already in desired state
