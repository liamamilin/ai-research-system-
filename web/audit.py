"""Append-only audit log for security-relevant operations."""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional


def _audit_path() -> str:
    try:
        from web.settings import get_settings
        return os.path.join(get_settings().paths.logs_dir, "audit.jsonl")
    except Exception:
        return "logs/audit.jsonl"


def log(
    action: str,
    user: Optional[str] = None,
    target: Optional[str] = None,
    result: str = "success",
    details: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
):
    """Append an audit event to the audit log file."""
    path = _audit_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "action": action,
        "user": user,
        "target": target,
        "result": result,
        "ip": ip,
        "details": details or {},
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def tail(limit: int = 100) -> list[dict]:
    """Return the last `limit` audit entries (most-recent first)."""
    path = _audit_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    result: list[dict] = []
    for line in reversed(lines[-limit:]):
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result
