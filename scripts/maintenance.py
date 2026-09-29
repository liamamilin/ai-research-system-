#!/usr/bin/env python3
"""Daily housekeeping for a project that runs unattended.

Everything here grew without bound because nothing ever pruned it:

* ``state/reports.db`` accumulates free pages as reports are re-indexed. It had
  reached 161 MB with ~25 MB of reclaimable space and no VACUUM ever run.
* ``users.db`` gained a session row per login *and* per token refresh, and
  ``cleanup_expired()`` existed but was never called from anywhere.
* ``file_lock`` leaves a ``.lock`` sidecar behind; 41 had piled up in state/.
* ``logs/openai`` held raw SDK request/response dumps.
* ``state/backups`` grew a directory per run, and per-save YAML copies.

The old ``cleanup_logs.py`` only globbed ``logs/*.log*`` -- not the subdirectories
where most of the volume actually was -- and nothing invoked it. This is the
single entry point, wired to launchd by ``install_launchd.sh``.

Usage:
    python scripts/maintenance.py --dry-run
    python scripts/maintenance.py
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_LOG_DAYS = 14
DEFAULT_BACKUPS = 14
# A lock sidecar older than this belongs to a process that is long gone.
DEFAULT_LOCK_DAYS = 7


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def vacuum_databases(state_dir: Path, dry_run: bool) -> str:
    """Reclaim free pages and checkpoint the WAL.

    Skipped while the app may be writing: VACUUM takes a lock, and failing to
    get one is reported rather than treated as success.
    """
    notes = []
    for name in ("reports.db", "users.db", "tracking.db", "events.db", "schedules.db"):
        path = state_dir / name
        if not path.is_file():
            continue
        try:
            conn = sqlite3.connect(path, timeout=10)
            try:
                free = conn.execute("PRAGMA freelist_count").fetchone()[0]
                page = conn.execute("PRAGMA page_size").fetchone()[0]
                reclaimable = free * page
                if dry_run:
                    notes.append(f"{name}: {reclaimable / 1048576:.1f} MB 可回收")
                    continue
                if reclaimable > 0:
                    conn.execute("VACUUM")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                notes.append(f"{name}: 回收 {reclaimable / 1048576:.1f} MB")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            # Busy means the app has it open; that is normal, not a failure.
            notes.append(f"{name}: 跳过（{exc}）")
    return "; ".join(notes)


def prune_sessions(dry_run: bool) -> str:
    """Delete expired session rows.

    The table only ever grew: two rows per login plus one per token refresh, so
    an idle tab refreshing every 15 minutes for a week adds hundreds.
    """
    try:
        from web.auth import db as user_db
    except Exception as exc:  # noqa: BLE001
        return f"跳过（{exc}）"
    override = user_db._DB_PATH_OVERRIDE
    try:
        # The maintenance script runs outside the app, so point it at the real
        # state dir rather than whatever the app happened to load.
        user_db._DB_PATH_OVERRIDE = str(
            Path(os.environ.get("AREC_STATE_DIR", "state")).resolve() / "users.db")
        if dry_run:
            with user_db.connect() as conn:
                total = conn.execute("SELECT count(*) FROM sessions").fetchone()[0]
                live = conn.execute(
                    "SELECT count(*) FROM sessions WHERE expires_at >= ?",
                    (int(time.time()),)).fetchone()[0]
            return f"sessions: {total - live}/{total} 条已过期"
        user_db.cleanup_expired()
        return "sessions: 已清理过期记录"
    except sqlite3.Error as exc:
        return f"sessions: 跳过（{exc}）"
    finally:
        user_db._DB_PATH_OVERRIDE = override


def prune_empty_round_dirs(output_dir: Path, days: int, dry_run: bool) -> str:
    """Remove date directories under the matrix that hold no reports.

    The round creates its directory before the first stage runs, so a round
    that was blocked by the budget guard, cancelled, or killed leaves an empty
    date directory behind. Four of them had accumulated. They are invisible in
    the report tree (which only lists directories that contain files) and the
    round is recorded properly in state/pipeline_rounds.json regardless, so an
    empty one is pure litter.

    Only directories directly under the pipeline root are considered, and only
    ones that match a date, so this can never walk into a report folder.
    """
    root = output_dir / "practical_ai_intelligence"
    if not root.is_dir():
        return "空轮次目录: 无可清理"
    cutoff = time.time() - days * 86400
    removed: list[str] = []
    for path in root.iterdir():
        # pathlib.glob("*/") ignores the trailing slash and matches files, so
        # this has to be an explicit is_dir() check.
        if not path.is_dir() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.name):
            continue
        try:
            if any(path.iterdir()):
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            if not dry_run:
                path.rmdir()
            removed.append(path.name)
        except OSError:
            continue
    if not removed:
        return "空轮次目录: 无可清理"
    return f"空轮次目录: 删除 {len(removed)} 个（{', '.join(sorted(removed))}）"


def prune_lock_sidecars(state_dir: Path, days: int, dry_run: bool) -> str:
    """Remove ``.lock`` sidecars left by file_lock.

    The flock is released when the handle closes, but the file is never
    unlinked, so every distinct path ever locked keeps one forever.
    """
    cutoff = time.time() - days * 86400
    removed = freed = 0
    stuck = 0
    for path in list(state_dir.glob("*.lock")) + list((state_dir / "locks").glob("*")):
        if not path.is_file() or path.suffix not in (".lock", ""):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
            removed += 1
            freed += size
        except OSError:
            # Counted rather than skipped: a sidecar that cannot be removed is
            # retried on every run for ever, and the report used to imply the
            # directory was clean.
            stuck += 1
    if not removed and not stuck:
        return "lock sidecars: 无可清理"
    report = f"lock sidecars: {removed} 个（{freed} 字节）"
    if stuck:
        report += f"，{stuck} 个删除失败"
    return report


def prune_logs(logs_dir: Path, days: int, dry_run: bool) -> str:
    """Delete old logs, including the subdirectories.

    The previous script globbed ``logs/*.log*`` only, so logs/schedules,
    logs/jobs and logs/openai -- where the bulk of the volume was -- could never
    be reached.
    """
    cutoff = time.time() - days * 86400
    removed = freed = 0
    stuck = 0
    for path in logs_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in (".log", ".jsonl", ".json") and ".log" not in path.name:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            size = path.stat().st_size
            if not dry_run:
                path.unlink()
            removed += 1
            freed += size
        except OSError:
            stuck += 1
    # Empty subdirectories left behind.
    if not dry_run:
        for path in sorted(logs_dir.rglob("*"), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                try:
                    path.rmdir()
                except OSError:
                    pass
    report = f"logs: {removed} 个文件（{freed / 1048576:.1f} MB）"
    if stuck:
        report += f"，{stuck} 个删除失败"
    return report


def prune_backups(state_dir: Path, keep: int, dry_run: bool) -> str:
    """Keep the newest N snapshots and config copies.

    Two independent caps: the snapshot directories come from backup_state.sh,
    the loose .yaml files from every config save via yaml_io.
    """
    root = state_dir / "backups"
    if not root.is_dir():
        return "backups: 无"
    freed = 0
    removed = 0
    stuck = 0

    def trim(entries: list[Path]) -> None:
        nonlocal freed, removed, stuck
        for path in entries[:max(0, len(entries) - keep)]:
            if dry_run:
                freed += _size(path)
                removed += 1
                continue
            # Counted only once the path is actually gone. This used
            # rmtree(ignore_errors=True) and credited the size either way, so a
            # snapshot that could not be deleted was still reported as removed
            # and as megabytes freed -- a report claiming to have reclaimed
            # space it had not, and claiming it again on every run since.
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError:
                stuck += 1
                continue
            if path.exists():
                stuck += 1
                continue
            freed += _size(path)
            removed += 1

    # is_dir()/suffix filtering rather than glob("*/"): on Python 3.10 a
    # trailing slash in the pattern is ignored, so glob("*/") matches the .yaml
    # files too and one cap was being applied to the combined population --
    # which deleted every snapshot directory while keeping config copies.
    trim(sorted(p for p in root.iterdir() if p.is_dir()))
    trim(sorted(root.glob("*.yaml")))
    report = f"backups: {removed} 项（{freed / 1048576:.1f} MB）"
    if stuck:
        report += f"，{stuck} 项删除失败"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="每日维护：回收数据库、清理会话/日志/锁/备份")
    parser.add_argument("--state-dir", default=str(ROOT / "state"))
    parser.add_argument("--logs-dir", default=str(ROOT / "logs"))
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    parser.add_argument("--log-days", type=int, default=DEFAULT_LOG_DAYS)
    parser.add_argument("--backups", type=int, default=DEFAULT_BACKUPS)
    parser.add_argument("--lock-days", type=int, default=DEFAULT_LOCK_DAYS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    logs_dir = Path(args.logs_dir)
    output_dir = Path(args.output_dir)
    os.environ.setdefault("AREC_STATE_DIR", str(state_dir))

    prefix = "[dry-run] " if args.dry_run else ""
    steps = (
        ("vacuum", lambda: vacuum_databases(state_dir, args.dry_run)),
        ("sessions", lambda: prune_sessions(args.dry_run)),
        ("locks", lambda: prune_lock_sidecars(state_dir, args.lock_days, args.dry_run)),
        ("logs", lambda: prune_logs(logs_dir, args.log_days, args.dry_run)),
        ("backups", lambda: prune_backups(state_dir, args.backups, args.dry_run)),
        ("empty-rounds", lambda: prune_empty_round_dirs(output_dir, args.lock_days, args.dry_run)),
    )
    failed: list[str] = []
    for name, step in steps:
        try:
            print(f"{prefix}{name}: {step()}", flush=True)
        except Exception as exc:  # noqa: BLE001 - one bad step must not stop the rest
            failed.append(name)
            print(f"{prefix}{name}: FAILED ({exc})", file=sys.stderr, flush=True)

    # Running every step even when one fails is the point -- a locked database
    # must not stop the logs being cleaned. But the exit code was 0 either way,
    # and this runs hourly from launchd, so a step that had been failing for a
    # week looked exactly like a clean run to anything reading the exit status.
    # The detail was printed to stderr, which nothing was checking.
    if failed:
        print(f"{len(failed)} step(s) failed: {', '.join(failed)}",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
