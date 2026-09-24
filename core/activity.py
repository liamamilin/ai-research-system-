"""Who is using the console right now.

The macOS launcher must decide when to close itself. Health alone cannot answer
that: a perfectly healthy service with nobody in front of it is exactly the
case the user wants the app to go away for, and conversely a long research run
should keep the window around.

So the service publishes one small file:

    state/app_activity.json
      {"last_activity": iso, "running": 2, "updated_at": iso}

The launcher polls it. Writing is throttled: a page load fires a burst of
requests and the file only needs to be roughly current, not exact.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

ACTIVITY_FILE = "app_activity.json"
# AppleScript has no JSON parser, so the launcher gets a trivial key=value file.
ACTIVITY_ENV = "app_activity.env"

# Rewriting this file on every request would be pure IO churn; the launcher
# only needs the timestamp to be accurate to the minute.
WRITE_THROTTLE_SECONDS = 20.0

_lock = threading.Lock()
_state_dir = "state"
_last_write = 0.0
_dirty = False
_last_activity = ""
_running = 0


def use_state_dir(state_dir: str) -> None:
    global _state_dir
    with _lock:
        _state_dir = state_dir


def activity_path() -> str:
    return os.path.join(_state_dir, ACTIVITY_FILE)


def activity_env_path() -> str:
    return os.path.join(_state_dir, ACTIVITY_ENV)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_locked() -> None:
    global _last_write, _dirty
    from core.fileio import atomic_write

    payload = {
        "last_activity": _last_activity,
        "running": _running,
        "updated_at": _now_iso(),
    }
    try:
        os.makedirs(_state_dir, exist_ok=True)
        atomic_write(activity_path(), json.dumps(payload, ensure_ascii=False, indent=1))
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        # The launcher parses this exact shape: AppleScript's `date` handles
        # "YYYY-MM-DD HH:MM:SS" regardless of the system locale, and its
        # integers are 32-bit so Unix epochs overflow.
        atomic_write(activity_env_path(),
                     f"last_activity={stamp}\nrunning={_running}\n")
        _last_write = time.time()
        _dirty = False
    except OSError:
        # Never let bookkeeping break a request.
        pass


def mark_activity(force: bool = False) -> None:
    """Record that a human just did something in the console."""
    global _dirty, _last_activity
    with _lock:
        _last_activity = _now_iso()
        _dirty = True
        if force or (time.time() - _last_write) >= WRITE_THROTTLE_SECONDS:
            _write_locked()


def set_running(count: int) -> None:
    """Record how many jobs are currently running."""
    global _dirty, _running
    with _lock:
        _running = max(0, int(count))
        _dirty = True
        _write_locked()


def snapshot() -> dict:
    """Current activity state (writes first if a write was throttled)."""
    with _lock:
        if _dirty and (time.time() - _last_write) >= WRITE_THROTTLE_SECONDS:
            _write_locked()
        return {"last_activity": _last_activity, "running": _running}


def read() -> dict:
    """Read the published file, as the launcher does. Empty when never written."""
    try:
        with open(activity_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def idle_seconds(now: Optional[float] = None) -> Optional[float]:
    """Seconds since the last recorded activity, or None when unknown."""
    epoch = _read_env_epoch()
    if epoch is None:
        return None
    return max(0.0, (now if now is not None else time.time()) - epoch)


def _read_env_epoch() -> Optional[float]:
    """Read the launcher-facing key=value file."""
    try:
        with open(activity_env_path(), "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("last_activity="):
                    return time.mktime(
                        time.strptime(line.split("=", 1)[1].strip(), "%Y-%m-%d %H:%M:%S"))
    except (OSError, ValueError):
        return None
    return None
