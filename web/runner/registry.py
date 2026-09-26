"""Central registry of running tasks."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Optional

from web.runner.log_bus import LogBus


class RunningTask:
    """Represents a single job execution instance."""

    def __init__(
        self,
        task_id: str,
        job_name: str,
        started_by: str,
        cancel_token: threading.Event,
        log_bus: LogBus,
        engine,
    ):
        self.task_id = task_id
        self.job_name = job_name
        self.started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        self.started_by = started_by
        self.cancel_token = cancel_token
        self.log_bus = log_bus
        self.engine = engine
        self.future: Optional[object] = None  # concurrent.futures.Future
        self.status: str = "running"  # running | success | failed | cancelled
        # Extra {placeholder} values for this run's prompt. The pipeline sets it
        # so a synthesis stage receives the upstream reports it depends on.
        self.prompt_vars: dict = {}

    @property
    def is_running(self) -> bool:
        return self.status == "running"


class TaskRegistry:
    """Singleton registry of running tasks, keyed by job_name."""

    _instance: Optional["TaskRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks: dict[str, RunningTask] = {}
            cls._instance._lock = threading.Lock()
        return cls._instance

    def _publish_activity(self) -> None:
        """Tell the launcher a run is in flight so it stays open."""
        try:
            from core import activity

            activity.set_running(len(self.all_running()))
        except Exception:  # noqa: BLE001 - bookkeeping must not break a run
            pass

    def start(self, job_name: str, started_by: str, engine) -> RunningTask:
        """Create and register a new RunningTask."""
        task_id = secrets.token_urlsafe(12)
        cancel_token = threading.Event()
        log_bus = LogBus(task_id)
        task = RunningTask(
            task_id=task_id,
            job_name=job_name,
            started_by=started_by,
            cancel_token=cancel_token,
            log_bus=log_bus,
            engine=engine,
        )
        with self._lock:
            # Cancel any previous running task for the same job
            prev = self._tasks.get(job_name)
            if prev and prev.is_running:
                prev.cancel_token.set()
            self._tasks[job_name] = task
        self._publish_activity()
        return task

    def get(self, job_name: str) -> Optional[RunningTask]:
        """Return the current (most recent) task for a job."""
        with self._lock:
            return self._tasks.get(job_name)

    def remove(self, job_name: str):
        with self._lock:
            self._tasks.pop(job_name, None)
        self._publish_activity()

    def cancel(self, job_name: str) -> bool:
        """Cancel a running task. Returns True if a task was cancelled."""
        task = self.get(job_name)
        if task and task.is_running:
            task.cancel_token.set()
            task.status = "cancelled"
            return True
        return False

    def is_running(self, job_name: str) -> bool:
        task = self.get(job_name)
        return bool(task and task.is_running)

    def all_running(self) -> dict[str, RunningTask]:
        """Return all currently running tasks."""
        with self._lock:
            return {k: v for k, v in self._tasks.items() if v.is_running}
