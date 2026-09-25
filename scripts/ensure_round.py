"""Catch-up runner: guarantee today's round happens, whoever triggers it.

launchd fires the daily job at 06:00 and a 30-minute interval job that calls
this script. The interval job is the safety net: if the calendar trigger was
missed (machine asleep, launchd wedged, a transient failure), the round still
happens — at most 30 minutes late instead of never.

Everything here is idempotent. It only starts a round when all of these hold:
  * no round is recorded for today, or today's round did not finish
  * no other pipeline run holds the lock
  * the monthly budget has not been exhausted

It also writes the scheduler heartbeat that /api/health/detailed reads, so a
dead scheduler is detected while it is dead rather than a day later when
someone notices a missing report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

STATE_DIR = os.path.join(REPO, "state")
HEARTBEAT = os.path.join(STATE_DIR, "scheduler_heartbeat.json")
ROUNDS = os.path.join(STATE_DIR, "pipeline_rounds.json")
RUN_SCRIPT = os.path.join(REPO, "scripts", "run_practical_intelligence.sh")
PYTHON = sys.executable

# A heartbeat older than this means the scheduler itself is not working.
HEARTBEAT_STALE_SECONDS = 40 * 60
# A round marked "running" for longer than this is not running: killing the
# launchd agent (a reinstall, `launchctl bootout`) takes the pipeline with it and
# leaves a registry entry that would block every future catch-up.
STALE_RUNNING_SECONDS = 3 * 3600

TRIGGER = "launchd"


def today() -> str:
    return time.strftime("%Y-%m-%d")


def _atomic_write(path: str, payload: dict) -> None:
    from core.fileio import atomic_write

    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=1))


def _read(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def round_state(date: str | None = None) -> dict:
    """What the registry says about today's round."""
    data = _read(ROUNDS)
    entry = data.get(date or today())
    if not isinstance(entry, dict):
        return {"exists": False, "status": "", "finished": False}
    status = str(entry.get("status") or "")
    stale = False
    if status == "running":
        started = entry.get("started_at") or ""
        stale = _older_than_seconds(started, STALE_RUNNING_SECONDS)
    return {
        "exists": True,
        "status": status,
        "trigger": str(entry.get("trigger") or ""),
        "finished": status in ("success", "partial"),
        "running": status == "running" and not stale,
        "stale": stale,
    }


def _older_than_seconds(stamp: str, seconds: float) -> bool:
    """True when a recorded ISO timestamp is older than ``seconds``."""
    text = str(stamp or "").strip()
    if not text:
        return False
    for candidate in (text, _with_colon_offset(text)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        now = datetime.now()
        if now.tzinfo is None:
            now = now.astimezone()
        return (now - parsed).total_seconds() > seconds
    return False


def _with_colon_offset(stamp: str) -> str:
    import re as _re
    return _re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", stamp)


def budget_ok() -> tuple[bool, str]:
    result = subprocess.run(
        [PYTHON, os.path.join(REPO, "run.py"), "--check-budget"],
        capture_output=True, text=True, cwd=REPO,
    )
    line = ""
    for candidate in (result.stdout or "").splitlines():
        if "budget" in candidate.lower():
            line = candidate.strip()
    return result.returncode == 0, line


def write_heartbeat(status: str, detail: str, *, ran: bool = False) -> None:
    previous = _read(HEARTBEAT)
    now = time.time()
    payload = {
        "status": status,
        "detail": detail,
        "checked_at": now,
        "checked_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "last_run": now if ran else previous.get("last_run", 0),
        "last_run_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z") if ran
        else previous.get("last_run_iso", ""),
        "trigger": TRIGGER,
    }
    _atomic_write(HEARTBEAT, payload)


def heartbeat_age(now: float | None = None) -> float | None:
    data = _read(HEARTBEAT)
    checked = data.get("checked_at")
    if not isinstance(checked, (int, float)):
        return None
    return max(0.0, (now if now is not None else time.time()) - checked)


def decide(*, state: dict | None = None, budget_allowed: bool = True,
           budget_note: str = "", dry_run: bool = False) -> tuple[bool, str]:
    """Whether a round should start now, and why."""
    if state is None:
        state = round_state()
    if state.get("stale"):
        return True, ("今日轮次标记为 running 但已超时（进程可能被杀），将重跑")
    if state.get("running"):
        return False, "今日轮次正在运行"
    if state.get("finished"):
        return False, f"今日轮次已完成（{state.get('status')}）"
    if not budget_allowed:
        return False, f"预算已用完，跳过本轮（{budget_note}）"
    reason = "今日无轮次记录" if not state.get("exists") else \
        f"今日轮次未完成（{state.get('status') or 'unknown'}）"
    if dry_run:
        return True, f"将启动：{reason}"
    return True, reason


def main() -> int:
    parser = argparse.ArgumentParser(description="Start today's round if it is missing")
    parser.add_argument("--dry-run", action="store_true",
                        help="decide and report without starting anything")
    parser.add_argument("--force", action="store_true",
                        help="start even if today's round is already finished")
    args = parser.parse_args()

    if args.force:
        should_run, reason = True, "强制启动"
    else:
        allowed, note = budget_ok()
        should_run, reason = decide(budget_allowed=allowed, budget_note=note,
                                    dry_run=args.dry_run)

    if not should_run:
        write_heartbeat("skipped", reason)
        print(f"[ensure_round] {reason}")
        return 0

    print(f"[ensure_round] {reason}")
    if args.dry_run:
        return 0

    # The lock makes this safe when the calendar trigger and the interval
    # trigger land in the same minute: whoever wins, the other exits here.
    from core.fileio import file_lock

    os.makedirs(STATE_DIR, exist_ok=True)
    lock_path = os.path.join(STATE_DIR, "pipeline.trigger.lock")
    try:
        with file_lock(lock_path, timeout=5):
            # Re-check under the lock: the other trigger may have just won.
            if not args.force:
                allowed, note = budget_ok()
                should_run, reason = decide(budget_allowed=allowed,
                                           budget_note=note)
                if not should_run:
                    write_heartbeat("skipped", reason)
                    print(f"[ensure_round] 锁内复查：{reason}")
                    return 0
            env = dict(os.environ, AREC_ROUND_TRIGGER=TRIGGER)
            result = subprocess.run(
                ["/bin/bash", RUN_SCRIPT], cwd=REPO, env=env)
            if result.returncode == 0:
                write_heartbeat("ok", "轮次完成", ran=True)
                return 0
            write_heartbeat("failed",
                            f"轮次退出码 {result.returncode}，下个周期会重试")
            return result.returncode or 1
    except TimeoutError:
        write_heartbeat("skipped", "另一个触发器正在处理")
        print("[ensure_round] 已有触发器在处理，退出")
        return 0


if __name__ == "__main__":
    sys.exit(main())
