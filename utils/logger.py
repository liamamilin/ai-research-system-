"""
Logging utility - unified logger setup.
"""

import os
import logging
from logging.handlers import RotatingFileHandler

# Third-party loggers that are too chatty at INFO level
_NOISY_LOGGERS = (
    "httpx",
    "httpcore",
    "openai",
    "urllib3",
    "duckduckgo_search",
    "watchfiles",
)


def setup_logger(
    name: str = "ai_research",
    log_file: str = None,
    level: str = "INFO",
) -> logging.Logger:
    """Configure logging with console and file handlers.

    Handlers are attached to the **root** logger so module loggers such as
    ``core.engine`` and ``core.research`` are captured too.
    """
    numeric = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric)
    root.handlers.clear()

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(numeric)
    ch.setFormatter(logging.Formatter("%(levelname)-5s %(message)s"))
    root.addHandler(ch)

    # File handler
    if log_file:
        d = os.path.dirname(log_file)
        if d:
            os.makedirs(d, exist_ok=True)
        fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setLevel(numeric)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
        root.addHandler(fh)

    logging.getLogger(name).setLevel(numeric)
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger(name)


def attach_file_logging(log_file: str, level: str = "INFO") -> None:
    """Add a rotating file handler to the root logger without clearing
    existing handlers (used by the web server, where uvicorn owns console
    logging but we still want job logs persisted to disk)."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    target = os.path.abspath(log_file)

    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and \
                os.path.abspath(getattr(handler, "baseFilename", "")) == target:
            return  # already attached

    d = os.path.dirname(log_file)
    if d:
        os.makedirs(d, exist_ok=True)
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    fh.setLevel(numeric)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(message)s"))
    root.addHandler(fh)

    if root.level == logging.NOTSET or root.level > numeric:
        root.setLevel(numeric)
    for noisy in _NOISY_LOGGERS:
        logging.getLogger(noisy).setLevel(logging.WARNING)
