"""Pipeline round registry shared by the web runner and the CLI/cron path.

The web UI already tracked rounds in ``state/pipeline_rounds.json`` (a
date-keyed mapping), but cron-driven runs were invisible: the Rounds page had
no history and health checks could not judge the last round. Both paths now
write the same file through this module.
"""

from __future__ import annotations

import json
import os
import time
from typing import Optional

from core.fileio import file_lock

STATE_FILE = os.path.join("state", "pipeline_rounds.json")
KEEP_ROUNDS = 20

TERMINAL_STATUSES = ("success", "partial", "failed", "cancelled", "interrupted")


def state_path(state_dir: Optional[str] = None) -> str:
    if state_dir:
        return os.path.join(state_dir, "pipeline_rounds.json")
    return STATE_FILE


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: str, data: dict) -> None:
    keys = sorted(data.keys(), reverse=True)[:KEEP_ROUNDS]
    trimmed = {k: data[k] for k in keys}
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def upsert_round(
    date: str,
    status: str,
    trigger: str = "",
    stages: Optional[list] = None,
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    state_dir: Optional[str] = None,
) -> dict:
    """Create or merge one round record. Never raises.

    Stages are merged by ``key`` so a cron stage update does not wipe stage
    detail written by the web runner (or vice versa).
    """
    path = state_path(state_dir)
    try:
        with file_lock(path):
            data = _read(path)
            entry = dict(data.get(date) or {})
            entry["date"] = date
            entry["status"] = status
            # The first writer defines the round's origin; later updates (stage
            # retries, cron finish after a web start) must not rewrite history.
            if not entry.get("trigger"):
                entry["trigger"] = trigger or "unknown"
            entry["started_at"] = started_at or entry.get("started_at") or now_iso()
            entry["finished_at"] = finished_at if finished_at is not None else entry.get("finished_at")

            merged: dict[str, dict] = {}
            for stage in (entry.get("stages") or []):
                key = stage.get("key") or stage.get("label") or ""
                if key:
                    merged[key] = dict(stage)
            for stage in stages or []:
                key = stage.get("key") or stage.get("label") or ""
                if not key:
                    continue
                current = merged.get(key, {})
                current.update({k: v for k, v in stage.items() if v is not None})
                merged[key] = current
            entry["stages"] = [merged[k] for k in sorted(merged)]

            data[date] = entry
            _write(path, data)
            return entry
    except (OSError, TimeoutError, ValueError):
        return {}


def list_rounds(state_dir: Optional[str] = None) -> list[dict]:
    """All recorded rounds, newest first."""
    data = _read(state_path(state_dir))
    rounds = [v for _date, v in sorted(data.items(), reverse=True) if isinstance(v, dict)]
    for entry in rounds:
        entry.setdefault("stages", [])
    return rounds


def latest_round(state_dir: Optional[str] = None) -> Optional[dict]:
    rounds = list_rounds(state_dir)
    return rounds[0] if rounds else None


def mark_interrupted(state_dir: Optional[str] = None) -> int:
    """Mark rounds left ``running`` by a crash as ``interrupted``."""
    path = state_path(state_dir)
    try:
        with file_lock(path):
            data = _read(path)
            changed = 0
            for date, entry in data.items():
                if isinstance(entry, dict) and entry.get("status") == "running":
                    entry["status"] = "interrupted"
                    entry["finished_at"] = entry.get("finished_at") or now_iso()
                    for stage in entry.get("stages") or []:
                        if stage.get("status") in ("running", "queued", "pending"):
                            stage["status"] = "interrupted"
                    data[date] = entry
                    changed += 1
            if changed:
                _write(path, data)
            return changed
    except (OSError, TimeoutError):
        return 0


def infer_stage_status(round_dir: str, stage_files: dict) -> list[dict]:
    """Derive stage records from the files a run actually produced.

    ``stage_files`` maps a stage key to the expected output filename.
    """
    stages: list[dict] = []
    for key, filename in stage_files.items():
        path = os.path.join(round_dir, filename)
        exists = os.path.isfile(path)
        size = os.path.getsize(path) if exists else 0
        stages.append({
            "key": key,
            "filename": filename,
            "status": "success" if exists and size > 0 else "missing",
            "size_bytes": size,
        })
    return stages
