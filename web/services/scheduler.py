"""Crontab-based scheduler management.

Reads and edits the user's crontab to schedule recurring job runs.
Works on macOS and Linux.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import subprocess
import time
import tempfile
from functools import wraps
from datetime import datetime, timedelta
from typing import Optional

from core import cron
from core.fileio import file_lock


class ScheduleConflict(ValueError):
    """A schedule with this ID already exists."""


def _serialized(func):
    @wraps(func)
    def wrapped(*args, **kwargs):
        # crontab is shared by all projects belonging to the same OS user.
        path = os.path.join(tempfile.gettempdir(), f"arec-crontab-{os.getuid()}")
        with file_lock(path):
            return func(*args, **kwargs)
    return wrapped


def _validate_job(job_id: str, schedule: str, command: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", job_id):
        raise ValueError("调度 ID 仅支持字母、数字、下划线、点和短横线，最长 128 字符")
    if not command.strip() or any(c in command for c in "\n\r\x00"):
        raise ValueError("执行命令不能为空或包含换行")
    cron.preview(schedule)
    return cron.parse_schedule(schedule)

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
    try:
        cron.parse_schedule(" ".join(fields))
    except ValueError:
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


@_serialized
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
    fields = list(_validate_job(job_id, schedule, command).values())

    lines = _get_crontab()
    # Check for duplicate id (exact match)
    for line in lines:
        m = _CRON_ID_RE.match(line)
        if m and m.group(1) == job_id:
            raise ScheduleConflict(f"job_id '{job_id}' 已存在")

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


@_serialized
def update_job(job_id: str, schedule: str, command: str) -> dict | None:
    """Edit in place, preserving paused state and unrelated crontab entries."""
    entry = _validate_job(job_id, schedule, command)
    lines = _get_crontab()
    indices = _find_job_lines(lines, job_id)
    if len(indices) < 2:
        return None
    enabled = not lines[indices[-1]].strip().startswith("#")
    lines[indices[-1]] = ("" if enabled else "# ") + " ".join(entry.values()) + " " + command
    _set_crontab(lines)
    return {**entry, "id": job_id, "command": command, "enabled": enabled}


@_serialized
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


@_serialized
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

    return True  # idempotent: the schedule exists and is already in this state


# ----- Missed-run detection -------------------------------------------------
# A cron entry that is active but has produced no evidence of running is the
# failure mode nobody notices: the report simply stops appearing. Each job is
# classified from the schedule plus whatever run evidence exists locally.

_MONTH_NAMES = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_DOW_NAMES = {d: i for i, d in enumerate(
    ["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}

# Runs may start late (model latency, retries); only count a run as missing
# after this much slack.
GRACE_MINUTES = 120
# How far back to look for an expected fire time.
LOOKBACK_HOURS = 366 * 24


def _resolve(part: str, names: Optional[dict]) -> int:
    """One cron field atom as a number, or -1 when it is not understood."""
    if names:
        named = names.get(part.lower())
        if named is not None:
            return named
    return int(part) if part.lstrip("-").isdigit() else -1


def _field_matches(field: str, value: int, low: int, high: int,
                   names: Optional[dict] = None) -> bool:
    """Standard 5-field cron matching (numbers, lists, ranges, steps, names)."""
    try:
        return value in cron.field_values(field, low, high, names)
    except ValueError:
        return False


def _matches(entry: dict, moment: "datetime") -> bool:
    return last_fire_before(entry, moment, lookback_hours=1) == moment.replace(second=0, microsecond=0)


def last_fire_before(entry: dict, when: "datetime",
                     lookback_hours: int = LOOKBACK_HOURS) -> Optional["datetime"]:
    """The most recent minute at which this schedule should have fired."""
    try:
        slots = cron.fire_times(entry, when, count=1, backwards=True,
                                days=max(1, lookback_hours // 24 + 1))
    except ValueError:
        return None
    if slots and when - slots[0] < timedelta(hours=lookback_hours):
        return slots[0]
    return None


def _pipeline_evidence(command: str, state_dir: str, repo_dir: str) -> Optional[dict]:
    """When the daily intelligence pipeline last actually ran.

    Two independent signals: the cron log the job appends to, and the round
    registry the engine writes. Either can be missing; the newer one wins.
    """
    signals: list[dict] = []
    log_name = "cron_pipeline.log"
    if "run_practical_intelligence" in command:
        log_path = os.path.join(repo_dir, "logs", log_name)
        if os.path.isfile(log_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(log_path))
            signals.append({"source": f"logs/{log_name}", "at": mtime.isoformat(timespec="seconds"),
                            "kind": "log"})
    try:
        from core import rounds

        # list_rounds is newest-first, but ordering is not what decides the
        # winner: comparing timestamps does, so a registry that gains entries
        # out of order (a backfill, a manual retry) still reports the real run.
        for round_row in rounds.list_rounds(state_dir)[:8]:
            finished = round_row.get("finished_at") or round_row.get("started_at")
            if not finished:
                continue
            signals.append({"source": "pipeline_rounds", "at": finished,
                            "kind": "round",
                            "trigger": round_row.get("trigger", ""),
                            "round": round_row.get("round_date") or round_row.get("date")})
    except Exception:  # noqa: BLE001 - evidence gathering must not fail health
        pass
    if not signals:
        return None
    # Copy before attaching the full list: the newest signal is itself a member
    # of `signals`, so assigning it in place builds a cycle and any JSON
    # encoder walking the result recurses forever.
    # Only a cron-triggered round proves the *schedule* fired. A hand-run proves
    # the pipeline works, and nothing about cron — counting it would let a broken
    # schedule stay "healthy" forever as long as someone ran it manually.
    cron_signals = [s for s in signals
                    if s.get("trigger") in ("cron", "launchd") or s.get("kind") == "log"]
    newest = dict(max(cron_signals or signals, key=lambda s: _sort_key(s["at"])))
    newest["all_signals"] = signals
    newest["cron_evidence"] = bool(cron_signals)
    return newest


def _evidence_for(job: dict, state_dir: str, repo_dir: str) -> Optional[dict]:
    command = job.get("command", "")
    if "run_practical_intelligence" in command:
        return _pipeline_evidence(command, state_dir, repo_dir)
    # Other jobs: their own log file, when the command redirects into logs/.
    match = re.search(r">>?\s*(?:logs/)?([\w.-]+\.log)", command)
    if match:
        log_path = os.path.join(repo_dir, "logs", match.group(1))
        if os.path.isfile(log_path):
            mtime = datetime.fromtimestamp(os.path.getmtime(log_path))
            return {"source": f"logs/{match.group(1)}",
                    "at": mtime.isoformat(timespec="seconds"), "kind": "log"}
    return None


def _as_aware(moment: "datetime") -> "datetime":
    """Naive timestamps are local time; make everything comparable.

    The round registry writes "+0800" while a log mtime is converted with
    isoformat(); mixing naive and aware datetimes raises at the first compare.
    """
    return moment.astimezone() if moment.tzinfo is None else moment


def _sort_key(value: str) -> float:
    """Comparable epoch for a timestamp; unparseable values sort oldest."""
    parsed = _parse_iso(value)
    if parsed is None:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def _parse_iso(value: str) -> Optional["datetime"]:
    """Parse a timestamp; strftime's %z gives "+0800", fromisoformat wants "+08:00"."""
    text = str(value or "").strip()
    candidates = [text]
    match = re.search(r"([+-]\d{2})(\d{2})$", text)
    if match:
        candidates.append(text[:match.start()] + match.group(1) + ":" + match.group(2))
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate)
        except (TypeError, ValueError):
            continue
    return None


def classify_jobs(jobs: list[dict], state_dir: str = "state",
                  repo_dir: str = ".", now: Optional["datetime"] = None) -> list[dict]:
    """Classify every cron job: ok / paused / overdue / unverified / no_schedule."""
    moment = _as_aware(now or cron.local_now())
    results: list[dict] = []
    for job in jobs:
        entry = job
        result = {
            "id": job.get("id"),
            "enabled": bool(job.get("enabled")),
            "schedule": " ".join(str(job.get(k, "*")) for k in
                                 ("minute", "hour", "day_of_month", "month", "day_of_week")),
            "command": (job.get("command") or "")[:160],
        }
        expected = last_fire_before(entry, moment)
        if expected is not None:
            expected = _as_aware(expected)
        result["last_expected_at"] = expected.isoformat(timespec="seconds") if expected else ""
        if not job.get("enabled"):
            result["status"] = "paused"
            result["detail"] = "调度已暂停，cron 不会触发该任务"
            results.append(result)
            continue
        if expected is None:
            result["status"] = "no_schedule"
            result["detail"] = "无法解析该 cron 表达式，未做漏跑判断"
            results.append(result)
            continue
        evidence = _evidence_for(job, state_dir, repo_dir)
        if not evidence:
            result["status"] = "unverified"
            result["detail"] = "该任务没有本地运行证据，无法判断是否漏跑"
            results.append(result)
            continue
        if not evidence.get("cron_evidence", True):
            result["status"] = "unverified"
            result["last_ran_at"] = evidence["at"]
            result["evidence"] = evidence
            result["detail"] = (
                "最近一次是手动运行，不能证明 cron 真的触发过；"
                "要判断调度是否漏跑，请等待下一个计划时间"
            )
            results.append(result)
            continue
        ran_at = _parse_iso(evidence["at"])
        if ran_at is not None:
            ran_at = _as_aware(ran_at)
        if ran_at is None:
            result["status"] = "unverified"
            result["detail"] = "运行证据时间无法解析"
            result["evidence"] = evidence
            results.append(result)
            continue
        overdue_slot = last_fire_before(entry, moment - timedelta(minutes=GRACE_MINUTES))
        result["last_ran_at"] = evidence["at"]
        result["evidence"] = evidence
        if ran_at >= expected:
            result["status"] = "ok"
            result["detail"] = f"最近一次实际运行：{ran_at.strftime('%m-%d %H:%M')}"
        elif overdue_slot is None or ran_at >= overdue_slot:
            # The slot is still inside the grace window: the run may simply be
            # slow (model latency, retries) rather than missing.
            result["status"] = "ok"
            result["detail"] = (
                f"应运行时间 {expected.strftime('%m-%d %H:%M')} 仍在宽限期内，"
                f"最近一次运行 {ran_at.strftime('%m-%d %H:%M')}"
            )
        else:
            overdue = moment - ran_at
            result["status"] = "overdue"
            result["missed_hours"] = round(overdue.total_seconds() / 3600, 1)
            result["detail"] = (
                f"上次应运行 {expected.strftime('%m-%d %H:%M')}，"
                f"但最近一次实际运行是 {ran_at.strftime('%m-%d %H:%M')}"
                f"（{result['missed_hours']} 小时前）"
            )
        results.append(result)
    return results


LAUNCHD_LABELS = ("com.arec.pipeline.daily", "com.arec.pipeline.catchup")


def list_launchd_agents() -> list[dict]:
    """The launchd agents that trigger the pipeline.

    launchd replaced cron here: it dispatches reliably on this machine, runs
    jobs missed while the machine slept, and can be inspected. Reading the
    agents means the schedule page and the watchdog see the real triggers
    instead of an empty crontab.
    """
    agents: list[dict] = []
    for label in LAUNCHD_LABELS:
        path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        config = {}
        try:
            with open(path, "rb") as fh:
                config = plistlib.load(fh)
        except (OSError, ValueError):
            pass
        if not isinstance(config, dict):
            config = {}
        info: dict = {"id": label, "backend": "launchd", "label": label,
                      "installed": bool(config)}
        try:
            result = subprocess.run(
                ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
                capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            if not config:
                continue
            calendar = config.get("StartCalendarInterval", {})
            info.update({"loaded": False, "enabled": False, "minute": "", "hour": "",
                         "command": "ensure_round.py", "interval_seconds": config.get("StartInterval", 0)})
            if isinstance(calendar, dict):
                info.update(hour=str(calendar.get("Hour", "")), minute=str(calendar.get("Minute", "")))
            agents.append(info)
            continue
        text = result.stdout
        lines = text.splitlines()
        # The first "state = " is the agent's own; deeper ones belong to nested
        # structures, and taking the last one reported "active" for everything.
        state = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("state = "):
                state = stripped.split("=", 1)[1].strip()
                break
        hour = minute = ""
        interval = 0
        # launchctl prints the calendar interval twice: as a stream declaration
        # ("Minute" => 0) and as a dict ("Minute" => 0). Both look the same.
        for i, line in enumerate(lines):
            if "calendarinterval" in line.lower() or "StartCalendarInterval" in line:
                for w in lines[i:i + 14]:
                    if '"hour"' in w.lower() and "=>" in w:
                        hour = w.split("=>")[-1].strip().rstrip(",")
                    if '"minute"' in w.lower() and "=>" in w:
                        minute = w.split("=>")[-1].strip().rstrip(",")
                if hour != "" and minute != "":
                    break
        for line in lines:
            if "run interval" in line.lower():
                digits = "".join(ch for ch in line.split("=")[-1] if ch.isdigit())
                if digits:
                    interval = int(digits)
        # "not running" is the normal state between calendar fires.
        loaded = True  # a successful launchctl print proves the agent is loaded
        calendar = config.get("StartCalendarInterval", {})
        if isinstance(calendar, dict):
            hour = hour or str(calendar.get("Hour", ""))
            minute = minute or str(calendar.get("Minute", ""))
        info.update({
            "loaded": loaded,
            "enabled": loaded,
            "state": state,
            "minute": minute,
            "hour": hour,
            "run_at_load": "RunAtLoad" in text,
            "interval_seconds": interval or config.get("StartInterval", 0),
            "command": (" ".join(config.get("ProgramArguments", [])) or ("run_practical_intelligence.sh"
                        if label.endswith("daily") else "ensure_round.py")),
        })
        agents.append(info)
    return agents


def all_jobs() -> list[dict]:
    """Every schedule that can trigger work: crontab entries and launchd agents."""
    error = None
    try:
        jobs = [{**job, "backend": "cron", "editable": True} for job in list_jobs()]
    except (RuntimeError, OSError) as exc:
        error = exc
        jobs = []
    for agent in list_launchd_agents():
        jobs.append({**agent, "schedule_type": "launchd", "editable": False})
    if error and not jobs:
        raise error
    if error:
        for job in jobs:
            job["discovery_warning"] = f"cron 无法读取：{error}"
    return jobs


def orphan_headers() -> list[str]:
    """cron_id headers whose command line is gone.

    A crontab rewritten by something else can keep the ``# cron_id:`` comment
    while losing the schedule line, which makes the job vanish silently: the
    list is empty, nothing errors, and the pipeline simply stops. This is what a
    truncated crontab looks like, and it is worth saying out loud.
    """
    try:
        lines = _get_crontab()
    except Exception:  # noqa: BLE001 - crontab may be unavailable
        return []
    listed = {job["id"] for job in list_jobs()}
    orphans: list[str] = []
    for line in lines:
        match = _CRON_ID_RE.match(line)
        if match and match.group(1) not in listed:
            orphans.append(match.group(1))
    return orphans


def _classify_launchd(jobs: list[dict], state_dir: str, now: datetime | None = None) -> list[dict]:
    """Status of the launchd agents, judged by the catch-up heartbeat."""
    heartbeat = {}
    path = os.path.join(state_dir, "scheduler_heartbeat.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            heartbeat = json.load(fh)
    except (OSError, ValueError):
        heartbeat = {}

    if not isinstance(heartbeat, dict):
        heartbeat = {}
    checked = heartbeat.get("checked_at")
    age = None
    if isinstance(checked, (int, float)):
        age = max(0.0, (now.timestamp() if now else time.time()) - checked)
    stale_after = 40 * 60

    results: list[dict] = []
    for job in jobs:
        if job.get("backend") != "launchd":
            continue
        entry = {
            "id": job["id"],
            "backend": "launchd",
            "enabled": bool(job.get("enabled")),
            "schedule": (f"{int(job['hour']):02d}:{int(job['minute']):02d}"
                         if job.get("hour") and job.get("minute")
                         else (f"每 {job['interval_seconds'] // 60} 分钟"
                               if job.get("interval_seconds") else "未知")),
            "command": job.get("command", ""),
        }
        if not job.get("loaded"):
            entry["status"] = "paused"
            entry["detail"] = "agent 未加载，launchd 不会执行它"
        elif job["id"].endswith(".daily"):
            # The catch-up heartbeat cannot prove that the daily agent fired.
            entry["status"] = "unverified"
            entry["detail"] = "每日触发器已加载；运行结果请查看轮次，补漏状态见补偿任务"
        elif age is None:
            entry["status"] = "unverified"
            entry["detail"] = "尚无调度器心跳，无法判断是否在派发"
        elif age > stale_after:
            entry["status"] = "overdue"
            entry["detail"] = f"调度器 {int(age // 60)} 分钟未心跳，agent 可能已失效"
        elif heartbeat.get("status") == "failed":
            entry["status"] = "overdue"
            entry["detail"] = f"上一次补漏失败：{heartbeat.get('detail', '')}"
        else:
            entry["status"] = "ok"
            entry["detail"] = f"{int(age)} 秒前心跳：{heartbeat.get('detail', '')}"
            if heartbeat.get("last_run_iso"):
                entry["last_ran_at"] = heartbeat["last_run_iso"]
        results.append(entry)
    return results


def schedule_health(state_dir: str = "state", repo_dir: str = ".",
                     now: Optional["datetime"] = None, jobs: list[dict] | None = None) -> dict:
    """Aggregate schedule state for the health report."""
    try:
        jobs = all_jobs() if jobs is None else jobs
    except Exception as exc:  # noqa: BLE001 - crontab may be unavailable
        return {"status": "unknown", "detail": f"无法读取调度任务：{exc}", "jobs": []}
    orphans = orphan_headers()
    if not jobs and not orphans:
        return {
            "status": "error",
            "detail": "没有任何调度任务：crontab 为空，且 launchd agent 未加载",
            "jobs": [], "overdue": 0, "paused": 0,
        }
    # A launchd agent's own heartbeat is its evidence, not the round registry.
    classified = [
        j for j in classify_jobs(
            [job for job in jobs if job.get("backend") != "launchd"],
            state_dir=state_dir, repo_dir=repo_dir, now=now)
    ]
    classified.extend(_classify_launchd(jobs, state_dir, now))
    overdue = [j for j in classified if j["status"] == "overdue"]
    paused = [j for j in classified if j["status"] == "paused"]
    if orphans:
        return {"status": "error", "detail": (
            f"crontab 里有 {len(orphans)} 个任务只剩注释头、调度行已丢失："
            + "、".join(orphans) + "（请检查 crontab 是否被覆盖）"),
            "jobs": classified, "overdue": len(overdue), "paused": len(paused),
            "orphan_headers": orphans}
    if overdue:
        status = "error"
        detail = (f"{len(overdue)} 个调度任务应运行但没有运行证据："
                  + "、".join(j["id"] for j in overdue))
    elif paused:
        status = "warn"
        detail = (f"{len(paused)} 个调度任务处于暂停状态，不会自动运行："
                  + "、".join(j["id"] for j in paused))
    elif any(j["status"] in ("unverified", "no_schedule") for j in classified):
        status = "warn"
        detail = "部分任务尚无独立运行证据，请查看各任务状态"
    else:
        status = "ok"
        detail = f"{len(classified)} 个调度任务均无漏跑迹象" if classified else "crontab 中没有调度任务"
    if any(job.get("discovery_warning") for job in jobs):
        status = "error" if status == "error" else "warn"
        detail += "；cron 读取失败，调度列表可能不完整"
    return {"status": status, "detail": detail, "jobs": classified,
            "overdue": len(overdue), "paused": len(paused)}


def describe_job(job: dict) -> dict:
    """Attach display metadata without pretending an interval has a fixed phase."""
    result = dict(job)
    if job.get("backend") == "launchd":
        daily = job["id"].endswith(".daily")
        result["title"] = "情报矩阵 · 每日运行" if daily else "情报矩阵 · 漏跑补偿"
        result["schedule_label"] = (f"每天 {int(job['hour']):02d}:{int(job['minute']):02d}"
                                    if daily and job.get("hour") and job.get("minute")
                                    else f"每 {job.get('interval_seconds', 0) // 60} 分钟检查")
        result["next_runs"] = []
        if daily and job.get("hour") and job.get("minute") and job.get("enabled"):
            result["next_runs"] = cron.preview(f"{job['minute']} {job['hour']} * * *")["next_runs"]
    else:
        result["schedule"] = " ".join(str(job[key]) for key in cron.KEYS)
        try:
            result["next_runs"] = cron.preview(result["schedule"])["next_runs"] if job.get("enabled") else []
        except ValueError:
            result["next_runs"] = []
    return result
