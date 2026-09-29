"""Tests for the state backup script."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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


def test_the_index_flag_actually_backs_up_the_index(tmp_path):
    """`--with-index` was dead on arrival.

    The block appending to FILES sat *above* the definition of the array it
    read, so every invocation died with "DERIVED[@]: unbound variable" -- the
    dry run and the real one alike. The report index is the largest database in
    the project and could not be backed up at all, and nothing here mentioned
    the flag.
    """
    base = _make_base(tmp_path)
    import sqlite3

    conn = sqlite3.connect(base / "state" / "reports.db")
    conn.execute("CREATE TABLE reports (path TEXT)")
    conn.execute("INSERT INTO reports VALUES ('a.md')")
    conn.commit()
    conn.close()

    dest = tmp_path / "out"
    dry = _run(["--with-index", "--dry-run", "--base", str(base), "--dest", str(dest)])
    assert dry.returncode == 0, dry.stderr
    assert "state/reports.db" in dry.stdout
    assert "skip (derived" not in dry.stdout

    real = _run(["--with-index", "--base", str(base), "--dest", str(dest)])
    assert real.returncode == 0, real.stderr
    assert (dest / "reports.db").is_file(), "--with-index copied nothing"
    assert "backed up 4 file(s)" in real.stdout


def test_the_index_is_left_out_by_default(tmp_path):
    base = _make_base(tmp_path)
    import sqlite3

    conn = sqlite3.connect(base / "state" / "reports.db")
    conn.execute("CREATE TABLE reports (path TEXT)")
    conn.commit()
    conn.close()

    dest = tmp_path / "out"
    result = _run(["--base", str(base), "--dest", str(dest)])
    assert result.returncode == 0
    assert not (dest / "reports.db").exists()
    assert "backed up 3 file(s)" in result.stdout


def test_a_snapshot_that_cannot_be_read_is_not_reported_as_backed_up(tmp_path):
    """A backup nobody verifies is only discovered to be broken at restore time.

    The databases are in WAL mode, and a plain `cp` of a WAL database copies
    the main file alone. Everything still in the `-wal` sidecar is simply absent
    from the snapshot, which then opens cleanly as a valid SQLite file and is
    quietly short of recent writes -- or, as measured here with a second
    connection open the way the running app holds one, missing whole tables,
    because the schema itself was still in the sidecar.

    So the snapshot is taken through the SQLite backup API and compared against
    the source row by row. `PRAGMA integrity_check` cannot catch this: a
    truncated database is still structurally valid.
    """
    import sqlite3

    base = _make_base(tmp_path)
    writer = sqlite3.connect(base / "state" / "users.db")
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE live (x INTEGER)")
    writer.execute("INSERT INTO live VALUES (7)")
    writer.commit()
    # A second connection is what keeps the WAL from being checkpointed, i.e.
    # what the running app looks like to anybody copying the file.
    holder = sqlite3.connect(base / "state" / "users.db")
    holder.execute("SELECT COUNT(*) FROM live").fetchone()
    try:
        assert (base / "state" / "users.db-wal").exists(), "test needs a live WAL"

        dest = tmp_path / "out"
        result = _run(["--base", str(base), "--dest", str(dest)])
        assert result.returncode == 0, result.stderr

        # `cp` of the main file alone loses the table entirely here.
        naive = tmp_path / "naive.db"
        shutil.copy(base / "state" / "users.db", naive)
        with sqlite3.connect(naive) as check:
            with pytest.raises(sqlite3.Error):
                check.execute("SELECT x FROM live").fetchall()

        restored = sqlite3.connect(dest / "users.db")
        assert restored.execute("SELECT x FROM live").fetchone()[0] == 7, \
            "the row that was only in the WAL did not survive the backup"
        restored.close()
    finally:
        holder.close()
        writer.close()


def test_an_unreadable_source_is_reported_rather_than_skipped_silently(tmp_path):
    base = _make_base(tmp_path)
    (base / "state" / "tracking.db").write_text("not a database", encoding="utf-8")

    dest = tmp_path / "out"
    result = _run(["--base", str(base), "--dest", str(dest)])
    assert "WARNING" in result.stderr, "a broken database was passed over quietly"
    assert "tracking.db" in result.stderr
    assert not (dest / "tracking.db").exists()
    assert result.returncode == 1, "an incomplete snapshot must not report success"
