"""Runtime health checks for the console.

``/api/health`` used to return a constant ``ok``, which meant a broken
database, unreadable config or a full disk still looked healthy. These checks
verify the real dependencies: config parsing, state writability, report and
tracking databases, index freshness, vector coverage, free disk and the last
pipeline round.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from typing import Optional

OK = "ok"
WARN = "warn"
ERROR = "error"

MIN_FREE_MB = 512
STALE_INDEX_FILES = 20


def _check(name: str, status: str, detail: str = "", **extra) -> dict:
    entry = {"name": name, "status": status, "detail": detail}
    entry.update(extra)
    return entry


def _config_check(config_dir: str = "config") -> dict:
    path = os.path.join(config_dir, "system.yaml")
    if not os.path.isfile(path):
        return _check("config", ERROR, f"missing {path}; system falls back to defaults")
    try:
        from core.config import load_system_config

        cfg = load_system_config(config_dir)
    except Exception as exc:  # noqa: BLE001 - health must never raise
        return _check("config", ERROR, f"{type(exc).__name__}: {exc}")
    model = (cfg.get("ai") or {}).get("model", "")
    provider = (cfg.get("search") or {}).get("provider", "")
    if not model:
        return _check("config", WARN, "ai.model not set (defaults apply)")
    return _check("config", OK, f"model={model}, search={provider or 'unset'}",
                  model=model, search_provider=provider)


def _paths_check(state_dir: str, output_dir: str) -> list[dict]:
    results = []
    for label, path in (("state_dir", state_dir), ("output_dir", output_dir)):
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".health-probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            results.append(_check(label, OK, f"writable: {os.path.abspath(path)}"))
        except OSError as exc:
            results.append(_check(label, ERROR, f"not writable: {exc}"))
    return results


def _sqlite_check(name: str, path: str, query: str) -> dict:
    if not os.path.exists(path):
        return _check(name, WARN, f"missing: {path}")
    if not os.path.isfile(path):
        return _check(name, ERROR, f"not a database file: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
        try:
            value = conn.execute(query).fetchone()[0]
        finally:
            conn.close()
        return _check(name, OK, f"{value} rows", value=value)
    except sqlite3.Error as exc:
        return _check(name, ERROR, f"{type(exc).__name__}: {exc}")


def _disk_check(path: str) -> dict:
    try:
        usage = shutil.disk_usage(path if os.path.exists(path) else ".")
        free_mb = usage.free // (1024 * 1024)
        status = OK if free_mb >= MIN_FREE_MB else (
            WARN if free_mb >= 64 else ERROR
        )
        return _check("disk_free", status, f"{free_mb} MB free", free_mb=free_mb)
    except OSError as exc:
        return _check("disk_free", WARN, f"unknown: {exc}")


def _index_freshness(output_dir: str, state_dir: str) -> dict:
    """Compare on-disk reports with indexed rows (watcher lag detection)."""
    reports_db = os.path.join(state_dir, "reports.db")
    if not os.path.isfile(reports_db):
        return _check("index_freshness", WARN, "reports.db missing")
    on_disk = 0
    for root, _dirs, files in os.walk(output_dir):
        on_disk += sum(1 for f in files if f.endswith((".md", ".mdx")))
    try:
        conn = sqlite3.connect(f"file:{reports_db}?mode=ro", uri=True, timeout=2)
        try:
            indexed = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _check("index_freshness", ERROR, f"{type(exc).__name__}: {exc}")

    missing = on_disk - indexed
    if missing > STALE_INDEX_FILES:
        return _check("index_freshness", WARN,
                      f"{missing} reports on disk not indexed", missing=missing)
    return _check("index_freshness", OK,
                  f"{indexed} indexed, {missing} pending", missing=missing)


def _vector_coverage(state_dir: str) -> dict:
    reports_db = os.path.join(state_dir, "reports.db")
    if not os.path.isfile(reports_db):
        return _check("vector_coverage", WARN, "reports.db missing")
    try:
        conn = sqlite3.connect(f"file:{reports_db}?mode=ro", uri=True, timeout=2)
        try:
            try:
                embedded = conn.execute("SELECT COUNT(*) FROM chunk_meta").fetchone()[0]
            except sqlite3.Error:
                return _check("vector_coverage", WARN, "vector index not built yet")
            total = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            orphan = conn.execute(
                "SELECT COUNT(*) FROM chunk_meta m WHERE NOT EXISTS"
                " (SELECT 1 FROM reports r WHERE r.path = m.path)"
            ).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _check("vector_coverage", ERROR, f"{type(exc).__name__}: {exc}")

    missing = max(0, total - embedded)
    base = {"missing": missing, "orphan_vectors": orphan}
    if missing == 0 and not orphan:
        return _check("vector_coverage", OK, f"{embedded}/{total} reports embedded", **base)
    detail = f"{embedded}/{total} reports embedded"
    if missing:
        detail += f"; {missing} lack vectors (QA falls back to FTS)"
    if orphan:
        detail += f"; {orphan} orphaned vector documents (run reindex to prune)"
    return _check("vector_coverage", WARN, detail, **base)


def _last_round(state_dir: str) -> dict:
    path = os.path.join(state_dir, "pipeline_rounds.json")
    if not os.path.isfile(path):
        return _check("last_round", WARN, "no pipeline round recorded")
    from core import rounds

    latest = rounds.latest_round(state_dir)
    if not latest:
        return _check("last_round", WARN, "no pipeline round recorded")
    status = latest.get("status", "unknown")
    when = latest.get("finished_at") or latest.get("started_at") or ""
    trigger = latest.get("trigger", "")
    stages = latest.get("stages") or []
    failed = [s for s in stages if s.get("status") in ("failed", "missing", "interrupted")]
    detail = f"{status} at {when or 'unknown'}"
    if trigger:
        detail += f" via {trigger}"
    if failed:
        detail += f"; {len(failed)}/{len(stages)} stages failed"
    entry_status = OK if status == "success" else (
        WARN if status in ("running", "pending", "partial") else ERROR
    )
    return _check("last_round", entry_status, detail,
                  round_status=status, failed_stages=len(failed), total_stages=len(stages))


def run_checks(state_dir: str = "state", output_dir: str = "output",
               config_dir: str = "config", deep: bool = False) -> dict:
    """Run health checks. ``deep`` adds index, vector and round inspection."""
    checks: list[dict] = [_config_check(config_dir)]
    checks += _paths_check(state_dir, output_dir)
    checks.append(_sqlite_check("reports_db", os.path.join(state_dir, "reports.db"),
                                "SELECT COUNT(*) FROM reports"))
    checks.append(_sqlite_check("tracking_db", os.path.join(state_dir, "tracking.db"),
                                "SELECT COUNT(*) FROM items"))
    checks.append(_disk_check(state_dir))
    if deep:
        checks.append(_index_freshness(output_dir, state_dir))
        checks.append(_vector_coverage(state_dir))
        checks.append(_last_round(state_dir))

    statuses = {c["status"] for c in checks}
    overall = ERROR if ERROR in statuses else (WARN if WARN in statuses else OK)
    return {
        "status": overall,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "deep": deep,
        "checks": checks,
        "errors": [c["name"] for c in checks if c["status"] == ERROR],
        "warnings": [c["name"] for c in checks if c["status"] == WARN],
    }


def stale_locks(state_dir: str = "state", stale_after: int = 3600) -> list[str]:
    """Lock files older than ``stale_after`` seconds (crash leftovers)."""
    lock_dir = os.path.join(state_dir, "locks")
    if not os.path.isdir(lock_dir):
        return []
    stale = []
    now = time.time()
    for fn in sorted(os.listdir(lock_dir)):
        path = os.path.join(lock_dir, fn)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > stale_after:
                stale.append(fn)
        except OSError:
            continue
    return stale
