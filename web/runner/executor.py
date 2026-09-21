"""Async job execution: runs ResearchEngine in a thread pool with logging bridge.

Thread safety: each job thread is isolated via per-task Logger and progress_cb.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from core.engine import ResearchEngine, CancelledError
from web.runner.handler import LogBusHandler
from web.runner.registry import RunningTask, TaskRegistry

logger = logging.getLogger("ai_research.web.runner")

# Shared thread pool (max 2 concurrent jobs)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job-runner")


def wrap_progress_event(event: dict) -> dict:
    """Normalize an engine progress event for the LogBus/SSE.

    Terminal events (status/error) keep their type so the SSE stream can
    detect completion. All other events are wrapped as ``type=progress``
    with ``event_type`` carrying the original kind (phase/round/search/
    tool/heartbeat) — otherwise the event's own ``type`` key would
    overwrite the envelope and the UI could not recognize it.
    """
    etype = event.get("type", "progress")
    if etype in ("status", "error"):
        return dict(event)
    payload = {k: v for k, v in event.items() if k != "type"}
    payload["event_type"] = etype
    return {"type": "progress", **payload}


def run_job_in_thread(task: RunningTask, config_dir: str, jobs_dir: str,
                      verbose: bool = False, date_override: str = None):
    """Run a job inside a thread.

    Creates a ResearchEngine with the task's cancel token and a progress callback
    that routes structured events to the log_bus.

    A dedicated child logger is created per task so captured log records
    from core/engine modules are attributed correctly without cross-talk.
    """

    def progress_cb(event: dict):
        """Route engine progress events to the task's LogBus."""
        task.log_bus.write(wrap_progress_event(event))

    engine = ResearchEngine(
        config_dir=config_dir,
        jobs_dir=jobs_dir,
        workspace_dir=os.path.dirname(os.path.abspath(config_dir)),
        cancel_token=task.cancel_token,
        progress_cb=progress_cb,
        date_override=date_override,
    )

    # Attach handler to the ROOT logger to capture core/engine log output
    root_logger = logging.getLogger()
    handler = LogBusHandler(task.log_bus, level=logging.INFO)
    root_logger.addHandler(handler)

    job_name = task.job_name

    try:
        result = engine.run_job(job_name, verbose=verbose)
        if task.cancel_token.is_set():
            task.status = "cancelled"
        elif result is not None:
            task.status = "success"
        else:
            task.status = "failed"
    except CancelledError:
        task.status = "cancelled"
    except Exception as e:
        task.status = "failed"
        task.log_bus.write({"type": "error", "message": f"Unhandled error: {e}"})
        logger.exception("Job %s failed with unhandled error", job_name)
    finally:
        root_logger.removeHandler(handler)
        task.log_bus.write({"type": "status", "status": task.status})


def start_job(job_name: str, started_by: str,
              config_dir: str = "config", jobs_dir: str = "jobs",
              verbose: bool = False) -> RunningTask:
    """Start a job asynchronously. Returns the RunningTask immediately."""
    registry = TaskRegistry()

    engine_stub = ResearchEngine(config_dir=config_dir, jobs_dir=jobs_dir)
    task = registry.start(job_name, started_by, engine_stub)

    task.log_bus.write({
        "type": "log",
        "level": "info",
        "message": f"Job '{job_name}' started by {started_by}",
    })

    future = _executor.submit(run_job_in_thread, task, config_dir, jobs_dir, verbose)
    task.future = future

    return task
