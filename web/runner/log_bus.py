"""Per-task ring buffer + fan-out for SSE log streaming.

Thread-safe: write() is called from worker threads, subscribe/unsubscribe
from the asyncio event loop thread. Delivery to subscribers uses
loop.call_soon_threadsafe to avoid corrupting asyncio.Queue internals.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from typing import Optional

_LOG_BUFFER_SIZE = 5000


class LogBus:
    """Per-task event bus.

    * Maintains a ring buffer of recent events (thread-safe via _lock).
    * Fans out new events to all SSE subscribers via call_soon_threadsafe.
    * Supports Last-Event-ID reconnection via replay().
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        self._buffer: deque[dict] = deque(maxlen=_LOG_BUFFER_SIZE)
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._counter = 0

    def write(self, event: dict):
        """Write an event and deliver to all subscribers.

        Can be safely called from any thread.
        """
        with self._lock:
            self._counter += 1
            event["_id"] = self._counter
            self._buffer.append(event)
            # Snapshot subscribers under lock, then deliver
            subs = list(self._subscribers)

        for q in subs:
            try:
                loop = q._loop if hasattr(q, "_loop") else None
                if loop and loop.is_running():
                    loop.call_soon_threadsafe(q.put_nowait, event.copy())
            except Exception:
                # Silently drop failed deliveries
                pass

    def replay(self, since_id: int = 0) -> list[dict]:
        """Return all events with _id > since_id (for SSE reconnection)."""
        # Work on a snapshot to avoid holding lock during copy
        with self._lock:
            events = list(self._buffer)
        return [e for e in events if e.get("_id", 0) > since_id]

    def subscribe(self) -> asyncio.Queue:
        """Add a subscriber queue. Returns the queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        with self._lock:
            self._subscribers.discard(q)

    @property
    def last_event_id(self) -> int:
        return self._counter
