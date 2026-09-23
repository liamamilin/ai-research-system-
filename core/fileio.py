"""Small filesystem primitives shared by core modules.

Atomic replace + advisory locking so state files survive crashes and
concurrent writers from multiple threads or processes.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

try:  # POSIX advisory locking; degrade to thread-only locking elsewhere
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_process_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _thread_lock(key: str) -> threading.Lock:
    with _registry_lock:
        return _process_locks.setdefault(key, threading.Lock())


@contextmanager
def file_lock(key: str, timeout: float = 10.0) -> Iterator[None]:
    """Serialize access to ``key`` across threads and processes.

    Uses a ``.lock`` sidecar file plus ``flock`` when available. The wait is
    bounded so a wedged holder cannot hang a request forever.
    """
    lock_path = f"{key}.lock"
    parent = os.path.dirname(lock_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    thread_lock = _thread_lock(os.path.abspath(lock_path))
    deadline = time.time() + max(0.0, timeout)
    if not thread_lock.acquire(timeout=max(0.001, timeout)):
        raise TimeoutError(f"could not acquire lock: {key}")

    handle: Optional[object] = None
    try:
        if fcntl is not None:
            handle = open(lock_path, "a+")
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() >= deadline:
                        raise TimeoutError(f"could not acquire lock: {key}")
                    time.sleep(0.02)
        yield
    finally:
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        thread_lock.release()


def atomic_write(path: str, content: str, encoding: str = "utf-8") -> None:
    """Write text via temp file + rename so readers never see a partial file."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise


def append_line(path: str, line: str, encoding: str = "utf-8") -> None:
    """Append one line under lock, flushing before close."""
    with file_lock(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
