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

logger = logging.getLogger(__name__)

_STATE_DIR = "state"
_HISTORY_DIR = "state/history"


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
            with open(path, "w") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)
            logger.debug("State updated for '%s': %s", job_name, status)
        except OSError as e:
            logger.error("Failed to write state for '%s': %s", job_name, e)

        # Also append to history
        StateManager.append_history(job_name, status,
                                    output_path=output_path,
                                    error=error,
                                    duration_seconds=duration_seconds,
                                    usage=usage)

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
    ):
        """Append a run record to the history file (append-only JSONL)."""
        _ensure_history_dir()
        path = _history_path(job_name)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "job_name": job_name,
            "status": status,
            "output_path": output_path,
            "error": error,
            "duration_seconds": duration_seconds,
            "usage": usage,
        }
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
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
    def get_usage_summary(days: int = 30) -> dict:
        """Aggregate token usage from all job history files.

        Returns totals plus per-day / per-job / per-model breakdowns for runs
        within the last ``days`` days (0 = all time).
        """
        _ensure_history_dir()
        cutoff = ""
        if days > 0:
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

        for fn in sorted(os.listdir(_HISTORY_DIR)):
            if not fn.endswith(".jsonl"):
                continue
            try:
                with open(os.path.join(_HISTORY_DIR, fn), "r", encoding="utf-8") as f:
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

                job_name = entry.get("job_name", fn.rsplit(".", 1)[0])
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

        return {
            "days": days,
            "totals": totals,
            "per_day": sorted(per_day.values(), key=lambda d: d["day"]),
            "per_job": sorted(per_job.values(),
                              key=lambda j: j["total_tokens"], reverse=True),
            "per_model": sorted(per_model.values(),
                                key=lambda m: m["total_tokens"], reverse=True),
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
