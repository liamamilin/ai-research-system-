"""
Job state tracking via JSON files.

Each enabled job records its last run result in a JSON file under
``state/<job_safe_name>.json``, including status, output path, error
message, and duration.

This data is used by ``run.py --status`` and is available as a data
source for future dashboard or notification features.
"""

import os
import json
import time
import logging
from typing import Optional

from core.fileio import append_line, atomic_write, file_lock

logger = logging.getLogger(__name__)

_STATE_DIR = "state"
_HISTORY_DIR = "state/history"
USAGE_FILE_NAME = "__usage__.jsonl"


def use_state_dir(state_dir: str) -> None:
    """Point state and history writes at ``<state_dir>``."""
    global _STATE_DIR, _HISTORY_DIR
    _STATE_DIR = state_dir
    _HISTORY_DIR = os.path.join(state_dir, "history")


def _job_state_path(job_name: str) -> str:
    safe = job_name.replace("/", "_").replace("\\", "_")
    return os.path.join(_STATE_DIR, f"{safe}.json")


def _history_path(job_name: str) -> str:
    safe = job_name.replace("/", "_").replace("\\", "_")
    return os.path.join(_HISTORY_DIR, f"{safe}.jsonl")


def _ensure_state_dir():
    os.makedirs(_STATE_DIR, exist_ok=True)


def _ensure_history_dir():
    os.makedirs(_HISTORY_DIR, exist_ok=True)


class StateManager:
    """Persist and query job execution state."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def update(
        job_name: str,
        status: str,  # "success" | "failed" | "skipped" | "cancelled"
        output_path: Optional[str] = None,
        error: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        usage: Optional[dict] = None,
        user: Optional[str] = None,
    ):
        """Record the result of a job run and append to history.

        ``usage`` carries LLM/search consumption for the run, e.g.
        ``{"model": "...", "requests": 3, "prompt_tokens": 1200,
        "completion_tokens": 800, "total_tokens": 2000, "searches": 4}``.
        """
        _ensure_state_dir()
        path = _job_state_path(job_name)

        entry = {
            "job_name": job_name,
            "last_run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "last_status": status,
            "last_output": output_path,
            "last_error": error,
            "last_duration_seconds": duration_seconds,
            "last_usage": usage,
        }

        try:
            with file_lock(path):
                atomic_write(path, json.dumps(entry, indent=2, ensure_ascii=False))
            logger.debug("State updated for '%s': %s", job_name, status)
        except (OSError, TimeoutError) as e:
            logger.error("Failed to write state for '%s': %s", job_name, e)

        # Also append to history
        StateManager.append_history(job_name, status,
                                    output_path=output_path,
                                    error=error,
                                    duration_seconds=duration_seconds,
                                    usage=usage,
                                    user=user)

    @staticmethod
    def get(job_name: str) -> Optional[dict]:
        """Return the latest state for a single job, or None."""
        path = _job_state_path(job_name)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def append_history(
        job_name: str,
        status: str,
        output_path: Optional[str] = None,
        error: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        usage: Optional[dict] = None,
        user: Optional[str] = None,
    ):
        """Append a run record to the history file (append-only JSONL)."""
        _ensure_history_dir()
        path = _history_path(job_name)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "job_name": job_name,
            "user": user or "",
            "status": status,
            "output_path": output_path,
            "error": error,
            "duration_seconds": duration_seconds,
            "usage": usage,
        }
        try:
            append_line(path, json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TimeoutError) as e:
            logger.error("Failed to write history for '%s': %s", job_name, e)

    @staticmethod
    def get_history(job_name: str, limit: int = 50) -> list[dict]:
        """Return the last `limit` history entries for a job (most-recent first)."""
        path = _history_path(job_name)
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        result = []
        for line in reversed(lines[-limit:]):
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return result

    @staticmethod
    def record_usage(kind: str, user: Optional[str], usage: Optional[dict]) -> None:
        """Record LLM usage for non-job activity (Q&A, reindex, API calls)."""
        if not usage:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "job_name": f"__{kind}__",
            "user": user or "",
            "status": "success",
            "usage": usage,
        }
        try:
            _ensure_history_dir()
            path = os.path.join(_HISTORY_DIR, USAGE_FILE_NAME)
            with file_lock(path):
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except (OSError, TimeoutError) as exc:
            logger.debug("usage record skipped: %s", exc)

    @staticmethod
    def get_usage_summary(days: int = 30, since: Optional[str] = None,
                          until: Optional[str] = None) -> dict:
        """Aggregate token usage from job history plus external usage records.

        ``since``/``until`` (ISO timestamps) take precedence over ``days`` so
        calendar-month windows do not silently include the previous month.
        """
        _ensure_history_dir()
        cutoff = since or ""
        if not cutoff and days > 0:
            cutoff = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z",
                time.localtime(time.time() - days * 86400),
            )

        totals = {
            "runs": 0,
            "runs_with_usage": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "searches": 0,
        }
        per_day: dict = {}
        per_job: dict = {}
        per_model: dict = {}
        per_user: dict = {}

        sources: list[tuple[str, str]] = []
        for fn in sorted(os.listdir(_HISTORY_DIR)):
            if fn.endswith(".jsonl"):
                fallback = "unknown" if fn == USAGE_FILE_NAME else fn.rsplit(".", 1)[0]
                sources.append((os.path.join(_HISTORY_DIR, fn), fallback))
        for file_path, fallback_name in sources:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            except OSError:
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = str(entry.get("ts", ""))
                if cutoff and ts < cutoff:
                    continue
                if until and ts > until:
                    continue

                totals["runs"] += 1
                usage = entry.get("usage") or {}
                if not usage:
                    continue
                totals["runs_with_usage"] += 1
                for key in ("prompt_tokens", "completion_tokens",
                            "total_tokens", "searches"):
                    totals[key] += int(usage.get(key) or 0)

                day = ts[:10] or "unknown"
                day_slot = per_day.setdefault(
                    day,
                    {"day": day, "runs": 0, "prompt_tokens": 0,
                     "completion_tokens": 0, "total_tokens": 0},
                )
                day_slot["runs"] += 1
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    day_slot[key] += int(usage.get(key) or 0)

                job_name = entry.get("job_name") or fallback_name
                job_slot = per_job.setdefault(
                    job_name,
                    {"job_name": job_name, "runs": 0, "total_tokens": 0},
                )
                job_slot["runs"] += 1
                job_slot["total_tokens"] += int(usage.get("total_tokens") or 0)

                model = usage.get("model") or "unknown"
                model_slot = per_model.setdefault(
                    model,
                    {"model": model, "runs": 0, "prompt_tokens": 0,
                     "completion_tokens": 0, "total_tokens": 0},
                )
                model_slot["runs"] += 1
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    model_slot[key] += int(usage.get(key) or 0)

                user = str(entry.get("user") or "") or "unknown"
                user_slot = per_user.setdefault(
                    user, {"user": user, "runs": 0, "total_tokens": 0, "searches": 0})
                user_slot["runs"] += 1
                user_slot["total_tokens"] += int(usage.get("total_tokens") or 0)
                user_slot["searches"] += int(usage.get("searches") or 0)

        return {
            "days": days,
            "since": cutoff,
            "until": until or "",
            "totals": totals,
            "per_day": sorted(per_day.values(), key=lambda d: d["day"]),
            "per_job": sorted(per_job.values(),
                              key=lambda j: j["total_tokens"], reverse=True),
            "per_model": sorted(per_model.values(),
                                key=lambda m: m["total_tokens"], reverse=True),
            "per_user": sorted(per_user.values(),
                               key=lambda u: u["total_tokens"], reverse=True),
        }

    @staticmethod
    def list_all() -> dict:
        """Return ``{job_name: state}`` for all jobs that have state files."""
        _ensure_state_dir()
        result = {}
        for fn in sorted(os.listdir(_STATE_DIR)):
            if not fn.endswith(".json"):
                continue
            path = os.path.join(_STATE_DIR, fn)
            try:
                with open(path, "r") as f:
                    state = json.load(f)
                key = state.get("job_name", fn.rsplit(".", 1)[0])
                result[key] = state
            except (json.JSONDecodeError, OSError):
                continue
        return result
