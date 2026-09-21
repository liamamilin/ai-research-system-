"""Logging handler that bridges Python logger output to a task's LogBus.

Filters by thread: when several jobs run concurrently (e.g. pipeline round
stages), each task's handler only forwards records emitted by its own
worker thread, so SSE streams stay separated.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from web.runner.log_bus import LogBus


class LogBusHandler(logging.Handler):
    """A logging.Handler that sends formatted log records to a LogBus."""

    def __init__(self, log_bus: LogBus, level: int = logging.INFO,
                 thread_id: Optional[int] = None):
        super().__init__(level)
        self.log_bus = log_bus
        # Default to the thread that constructs the handler (the job runner
        # thread), which is also the thread the engine logs from.
        self.thread_id = thread_id if thread_id is not None else threading.get_ident()
        self.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))

    def emit(self, record: logging.LogRecord):
        if record.thread != self.thread_id:
            return
        msg = self.format(record)
        try:
            self.log_bus.write({
                "type": "log",
                "ts": record.created,
                "level": record.levelname.lower(),
                "message": msg,
            })
        except Exception:
            self.handleError(record)
