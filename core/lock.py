"""
File-based lock manager to prevent concurrent execution of the same job.

Lock files are stored in ``state/locks/<job_safe_name>.lock``, each
containing a JSON payload with the PID and start time.

Stale detection:
  If a lock file is older than ``stale_after`` seconds it is considered
  orphaned (e.g. from a crash) and automatically released on the next
  acquisition attempt.
"""

import os
import json
import time
import logging
import threading
from typing import Optional

from core.fileio import atomic_write, file_lock

logger = logging.getLogger(__name__)

_LOCK_DIR = "state/locks"
_DEFAULT_STALE = 3600  # 1 hour


def _job_lock_path(job_name: str) -> str:
    safe = job_name.replace("/", "_").replace("\\", "_")
    return os.path.join(_LOCK_DIR, f"{safe}.lock")


def _ensure_lock_dir():
    os.makedirs(_LOCK_DIR, exist_ok=True)


class LockAcquireError(Exception):
    """Raised when the lock cannot be acquired because the job is already
    running (and the lock is not stale)."""


class LockManager:
    """Context manager for per-job file locks.

    Usage::

        with LockManager(job_name, stale_after=1800):
            ...  # exclusive access
    """

    def __init__(self, job_name: str, stale_after: int = _DEFAULT_STALE,
                 owner_id: Optional[str] = None):
        self.job_name = job_name
        self.lock_path = _job_lock_path(job_name)
        self.stale_after = stale_after
        # Thread id in the default owner key: two threads in one process are
        # two owners, so a second run of the same job cannot "re-enter" the lock.
        self._owner_id = owner_id or f"{os.getpid()}:{threading.get_ident()}"
        self._released = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> bool:
        """Try to acquire the lock.

        Returns True if the lock was acquired, False if the job is
        already running and the lock is not stale.

        Callers that want an exception on failure should use the
        context manager interface (``with LockManager(...)``), which
        raises ``LockAcquireError`` when acquisition fails.
        """
        _ensure_lock_dir()
        payload = self._read_lock()

        if payload is not None:
            # Same owner → re-entrant, allow
            if payload.get("owner_id") == self._owner_id:
                logger.debug("Lock re-acquired by same owner '%s' for '%s'",
                             self._owner_id, self.job_name)
                self._released = False
                return True

            if self._is_stale(payload):
                logger.warning(
                    "Lock for '%s' is stale (age=%ds), releasing.",
                    self.job_name,
                    int(time.time() - payload["started_at"]),
                )
                self._force_release()
            else:
                logger.info(
                    "Job '%s' is already running (PID %s, started at %s).",
                    self.job_name,
                    payload["pid"],
                    time.strftime(
                        "%Y-%m-%dT%H:%M:%S", time.localtime(payload["started_at"])
                    ),
                )
                return False

        with file_lock(self.lock_path):
            self._write_lock()
        logger.debug("Lock acquired for '%s' at %s", self.job_name, self.lock_path)
        return True

    def release(self):
        """Release the lock explicitly (normally called via the context
        manager, but can be called directly)."""
        if self._released:
            return
        # Only release if we still own it (check again for stale detection)
        payload = self._read_lock()
        if payload and payload.get("owner_id") == self._owner_id:
            self._force_release()
        self._released = True
        logger.debug("Lock released for '%s'", self.job_name)

    @classmethod
    def is_locked(cls, job_name: str) -> bool:
        """Check whether a lock is currently held (and not stale)."""
        path = _job_lock_path(job_name)
        if not os.path.isfile(path):
            return False
        try:
            with open(path, "r") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            return False
        age = time.time() - payload.get("started_at", 0)
        if age > _DEFAULT_STALE:
            return False
        return True

    @classmethod
    def force_release(cls, job_name: str):
        """Remove a lock file unconditionally."""
        path = _job_lock_path(job_name)
        try:
            if os.path.isfile(path):
                os.remove(path)
                logger.debug("Force-released lock for '%s'", job_name)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self):
        ok = self.acquire()
        if not ok:
            raise LockAcquireError(
                f"Job '{self.job_name}' is already running. "
                "Set skip_if_running=false or wait for it to finish."
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False  # do not suppress exceptions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read_lock(self) -> Optional[dict]:
        try:
            with open(self.lock_path, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _write_lock(self):
        payload = {
            "pid": os.getpid(),
            "started_at": time.time(),
            "thread": threading.current_thread().name,
            "owner_id": self._owner_id,
        }
        atomic_write(self.lock_path, json.dumps(payload))

    def _force_release(self):
        try:
            if os.path.isfile(self.lock_path):
                os.remove(self.lock_path)
        except OSError:
            pass

    def _is_stale(self, payload: dict) -> bool:
        age = time.time() - payload.get("started_at", 0)
        return age > self.stale_after
