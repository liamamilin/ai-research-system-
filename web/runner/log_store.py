"""Durable run logs for web-triggered job runs.

The in-memory :class:`LogBus` only lives as long as the process, so a page
refresh or a server restart made finished runs' logs disappear. Each run's
events are also appended to ``logs/jobs/<job>.jsonl`` (one JSON object per
line, tagged with ``run_id``) and can be replayed through the API.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

MAX_LOG_BYTES = 5 * 1024 * 1024
KEEP_ROTATIONS = 1

_lock = threading.Lock()


def job_log_path(logs_dir: str, job_name: str) -> str:
    safe = job_name.replace("/", "_").replace("\\", "_").replace("..", "_")
    return os.path.join(logs_dir, "jobs", f"{safe}.jsonl")


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) < MAX_LOG_BYTES:
            return
    except OSError:
        return
    for i in range(KEEP_ROTATIONS, 0, -1):
        src = f"{path}.{i}"
        dst = f"{path}.{i + 1}"
        try:
            if os.path.isfile(src):
                os.replace(src, dst)
        except OSError:
            continue
    try:
        os.replace(path, f"{path}.1")
    except OSError:
        pass


def append_event(logs_dir: str, job_name: str, run_id: str, event: dict) -> None:
    """Append one event for a run. Never raises: logging must not break runs."""
    path = job_log_path(logs_dir, job_name)
    record = {
        "run_id": run_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
    }
    try:
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return


def read_events(logs_dir: str, job_name: str, run_id: Optional[str] = None,
                limit: int = 2000) -> tuple[list[dict], list[dict]]:
    """Return ``(runs, events)`` for a job's persisted log.

    ``runs`` is a newest-first summary per run id; ``events`` belongs to
    ``run_id`` when given, otherwise to the most recent run.
    """
    path = job_log_path(logs_dir, job_name)
    if not os.path.isfile(path):
        return [], []

    per_run: dict[str, list[dict]] = {}
    order: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                rid = str(record.get("run_id") or "unknown")
                if rid not in per_run:
                    per_run[rid] = []
                    order.append(rid)
                per_run[rid].append(record)
    except OSError:
        return [], []

    runs: list[dict] = []
    for rid in order:
        records = per_run[rid]
        status = "unknown"
        started = records[0].get("ts", "") if records else ""
        finished = ""
        for record in records:
            event = record.get("event") or {}
            if event.get("type") == "status":
                status = str(event.get("status") or status)
                finished = record.get("ts", "")
        runs.append({
            "run_id": rid,
            "status": status,
            "started_at": started,
            "finished_at": finished,
            "events": len(records),
        })
    runs.reverse()

    target = run_id or (runs[0]["run_id"] if runs else None)
    events = per_run.get(target, []) if target else []
    if limit and len(events) > limit:
        events = events[-limit:]
    return runs, events
