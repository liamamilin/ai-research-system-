"""Daily maintenance: the things that only get worse if nothing prunes them.

Every case runs against a temp tree, never the project's real state.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "arec_maintenance", ROOT / "scripts" / "maintenance.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


maintenance = _load()


@pytest.fixture()
def tree(tmp_path):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    (state / "locks").mkdir(parents=True)
    (logs / "schedules").mkdir(parents=True)
    (logs / "openai").mkdir(parents=True)

    conn = sqlite3.connect(state / "reports.db")
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [("y" * 400,)] * 800)
    conn.commit()
    conn.close()
    # Deleting rows leaves free pages that only VACUUM returns.
    conn = sqlite3.connect(state / "reports.db")
    conn.execute("DELETE FROM t")
    conn.commit()
    conn.close()

    users = sqlite3.connect(state / "users.db")
    users.execute("CREATE TABLE sessions (jti TEXT, expires_at INTEGER)")
    now = int(time.time())
    users.executemany("INSERT INTO sessions VALUES (?, ?)",
                      [(f"live{i}", now + 3600) for i in range(3)]
                      + [(f"dead{i}", now - 3600) for i in range(9)])
    users.commit()
    users.close()
    return tmp_path, state, logs


def _db_size(path: Path) -> int:
    return path.stat().st_size


# --- vacuum ------------------------------------------------------------------

def test_vacuum_returns_free_pages(tree):
    tmp_path, state, _logs = tree
    before = _db_size(state / "reports.db")
    assert maintenance.vacuum_databases(state, dry_run=False)
    after = _db_size(state / "reports.db")
    assert after < before, "free pages were not reclaimed"


def test_dry_run_changes_nothing(tree):
    tmp_path, state, _logs = tree
    before = _db_size(state / "reports.db")
    note = maintenance.vacuum_databases(state, dry_run=True)
    assert "可回收" in note
    assert _db_size(state / "reports.db") == before


def test_vacuum_reports_rather_than_crashing_when_busy(tree, monkeypatch):
    """The app usually has the database open; that is not a failure."""
    _tmp, state, _logs = tree

    class Busy:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, *a):
            if "VACUUM" in sql.upper() or "freelist" in sql.lower():
                raise sqlite3.OperationalError("database is locked")
            return self

        def fetchone(self):
            return (0,)

        def close(self):
            pass

    monkeypatch.setattr(maintenance.sqlite3, "connect", lambda *a, **k: Busy())
    note = maintenance.vacuum_databases(state, dry_run=False)
    assert "跳过" in note


# --- sessions ----------------------------------------------------------------

def test_expired_sessions_are_removed_and_live_ones_kept(tree, monkeypatch):
    _tmp, state, _logs = tree
    monkeypatch.setenv("AREC_STATE_DIR", str(state))
    from web.auth import db as user_db
    monkeypatch.setattr(user_db, "_DB_PATH_OVERRIDE", str(state / "users.db"))

    maintenance.prune_sessions(dry_run=False)
    remaining = {row[0] for row in
                 sqlite3.connect(state / "users.db").execute("SELECT jti FROM sessions")}
    assert remaining == {"live0", "live1", "live2"}


def test_dry_run_reports_the_expired_count_without_deleting(tree, monkeypatch):
    _tmp, state, _logs = tree
    monkeypatch.setenv("AREC_STATE_DIR", str(state))
    from web.auth import db as user_db
    monkeypatch.setattr(user_db, "_DB_PATH_OVERRIDE", str(state / "users.db"))
    note = maintenance.prune_sessions(dry_run=True)
    assert "9/12" in note
    total = sqlite3.connect(state / "users.db").execute(
        "SELECT count(*) FROM sessions").fetchone()[0]
    assert total == 12


# --- logs --------------------------------------------------------------------

def test_old_logs_are_pruned_including_subdirectories(tree):
    """The old script globbed logs/*.log* only, so these were unreachable."""
    _tmp, _state, logs = tree
    old = time.time() - 90 * 86400
    fresh = time.time()
    (logs / "old.log").write_text("x")
    (logs / "schedules" / "run.log").write_text("x")
    (logs / "openai" / "dump.json").write_text("x")
    (logs / "jobs" / "events.jsonl").write_text("x") if (logs / "jobs").mkdir() is None else None
    for path in logs.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    (logs / "fresh.log").write_text("x")
    os.utime(logs / "fresh.log", (fresh, fresh))

    note = maintenance.prune_logs(logs, days=14, dry_run=False)
    assert not (logs / "old.log").exists()
    assert not (logs / "schedules" / "run.log").exists(), "subdirectory was skipped"
    assert not (logs / "openai" / "dump.json").exists(), "subdirectory was skipped"
    assert (logs / "fresh.log").exists(), "a recent log was deleted"


def test_empty_log_directories_are_tidied_up(tree):
    _tmp, _state, logs = tree
    old = time.time() - 90 * 86400
    (logs / "schedules" / "run.log").write_text("x")
    os.utime(logs / "schedules" / "run.log", (old, old))
    maintenance.prune_logs(logs, days=14, dry_run=False)
    assert not (logs / "schedules").exists()


# --- lock sidecars -----------------------------------------------------------

def test_stale_lock_sidecars_are_removed(tree):
    _tmp, state, _logs = tree
    old = time.time() - 30 * 86400
    recent = time.time()
    stale = state / "practical_ai_intelligence_01.json.lock"
    fresh = state / "live.json.lock"
    nested = state / "locks" / "another.lock"
    for path in (stale, fresh, nested):
        path.write_bytes(b"")
    os.utime(stale, (old, old))
    os.utime(nested, (old, old))
    os.utime(fresh, (recent, recent))

    maintenance.prune_lock_sidecars(state, days=7, dry_run=False)
    assert not stale.exists(), "a stale sidecar survived"
    assert not nested.exists()
    assert fresh.exists(), "a live sidecar was removed"


# --- backups -----------------------------------------------------------------

def test_backups_are_capped(tree):
    _tmp, state, _logs = tree
    backups = state / "backups"
    backups.mkdir()
    for index in range(6):
        (backups / f"2026010{index}_000000").mkdir()
        (backups / f"2026010{index}_000000" / "users.db").write_bytes(b"x" * 100)
    for index in range(6):
        (backups / f"cfg_{index}.yaml").write_text("x: 1")

    maintenance.prune_backups(state, keep=2, dry_run=False)
    snapshots = sorted(p.name for p in backups.iterdir() if p.is_dir())
    assert len(snapshots) == 2
    assert snapshots == ["20260104_000000", "20260105_000000"], "kept the wrong ones"
    assert len(list(backups.glob("*.yaml"))) == 2


def test_dry_run_leaves_backups_alone(tree):
    _tmp, state, _logs = tree
    backups = state / "backups"
    backups.mkdir()
    for index in range(5):
        (backups / f"2026010{index}_000000").mkdir()
    maintenance.prune_backups(state, keep=1, dry_run=True)
    assert len([p for p in backups.iterdir() if p.is_dir()]) == 5


# --- the CLI -----------------------------------------------------------------

def test_dry_run_touches_nothing(tree, capsys):
    tmp_path, state, logs = tree
    before = _db_size(state / "reports.db")
    import sys as _sys
    argv = _sys.argv
    _sys.argv = ["maintenance.py", "--dry-run",
                 "--state-dir", str(state), "--logs-dir", str(logs)]
    try:
        assert maintenance.main() == 0
    finally:
        _sys.argv = argv
    assert _db_size(state / "reports.db") == before
    assert "[dry-run]" in capsys.readouterr().out


def test_one_failing_step_does_not_stop_the_others(tree, monkeypatch, capsys):
    """Maintenance runs unattended at 04:30; a single failure must not abort it."""
    tmp_path, state, logs = tree

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(maintenance, "vacuum_databases", boom)
    import sys as _sys
    argv = _sys.argv
    _sys.argv = ["maintenance.py", "--state-dir", str(state), "--logs-dir", str(logs)]
    try:
        assert maintenance.main() == 0
    finally:
        _sys.argv = argv
    captured = capsys.readouterr()
    assert "FAILED" in captured.err
    # The later steps still ran, which is the whole point.
    for step in ("sessions:", "lock sidecars:", "logs:", "backups:"):
        assert step in captured.out, f"{step} did not run"


# --- wiring ------------------------------------------------------------------

def test_maintenance_runs_on_a_schedule():
    """Unreferenced housekeeping is how state/ reached 161 MB."""
    script = (ROOT / "scripts" / "install_launchd.sh").read_text(encoding="utf-8")
    assert "com.arec.maintenance" in script
    plist = ROOT / "scripts" / "launchd" / "com.arec.maintenance.plist"
    assert plist.is_file()
    body = plist.read_text(encoding="utf-8")
    assert "StartCalendarInterval" in body and "maintenance.py" in body


def test_deprecated_cleanup_script_delegates():
    """It used to be dead code that could only ever see logs/*.log*."""
    body = (ROOT / "scripts" / "cleanup_logs.py").read_text(encoding="utf-8")
    assert "maintenance" in body
    assert "prune_logs" in body
