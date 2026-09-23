"""Tests for the state backup script."""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_state.sh"


def _make_base(tmp_path) -> Path:
    import sqlite3

    base = tmp_path / "proj"
    (base / "state").mkdir(parents=True)
    (base / "config").mkdir()
    conn = sqlite3.connect(base / "state" / "users.db")
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    (base / "state" / "report_meta.jsonl").write_text("{}", encoding="utf-8")
    (base / "config" / "system.yaml").write_text("ai: {}\n", encoding="utf-8")
    return base


def _run(args, **kwargs):
    return subprocess.run(["bash", str(SCRIPT)] + args,
                          capture_output=True, text=True, **kwargs)


def test_dry_run_lists_without_writing(tmp_path):
    base = _make_base(tmp_path)
    dest = tmp_path / "out"
    result = _run(["--dry-run", "--base", str(base), "--dest", str(dest)])
    assert result.returncode == 0
    assert "would copy: state/users.db" in result.stdout
    assert "skip (missing): state/tracking.db" in result.stdout
    assert not dest.exists()


def test_backup_copies_files(tmp_path):
    base = _make_base(tmp_path)
    dest = tmp_path / "out"
    result = _run(["--base", str(base), "--dest", str(dest)])
    assert result.returncode == 0
    assert (dest / "users.db").read_bytes().startswith(b"SQLite format 3")
    assert (dest / "report_meta.jsonl").is_file()
    assert (dest / "system.yaml").is_file()
    assert "backed up 3 file(s)" in result.stdout


def test_backup_nothing_to_do(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    dest = tmp_path / "out"
    result = _run(["--base", str(empty), "--dest", str(dest)])
    assert result.returncode == 0
    assert "nothing to back up" in result.stdout
    assert not dest.exists()


def test_unknown_option_fails(tmp_path):
    result = _run(["--nope"])
    assert result.returncode == 2
