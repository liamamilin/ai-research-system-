"""Every database in the project now shares one connection setup.

Five ``connect()`` helpers had drifted into three different PRAGMA
configurations. The default ``busy_timeout`` is zero, so a read racing a write
raises ``database is locked`` rather than waiting -- and the Web console is
multi-threaded, with ``users.db`` read on every authenticated request and
written on every login.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from core import sqlite_util


def _pragma(conn, name):
    return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_a_new_database_is_created_in_wal(tmp_path):
    path = str(tmp_path / "nested" / "deeper" / "x.db")
    with sqlite_util.connect(path) as conn:
        conn.execute("CREATE TABLE t (x)")
    assert sqlite_util.is_wal(path) is True


def test_the_timeout_is_installed_before_the_journal_switch(tmp_path):
    """Order is load-bearing, not cosmetic.

    Switching the journal mode takes a brief exclusive lock. A connection that
    has not yet installed a busy timeout does not wait for it -- it raises. So
    two connections switching at once would turn a harmless startup race into
    an outage. core/schedule_store.py learned this the hard way; here it is
    pinned by a test instead.
    """
    order: list[str] = []
    real_connect = sqlite3.connect

    class _Conn:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *a, **k):
            order.append(sql)
            return self._inner.execute(sql, *a, **k)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkey = pytest.MonkeyPatch()
    monkey.setattr(sqlite_util.sqlite3, "connect",
                   lambda *a, **k: _Conn(real_connect(*a, **k)))
    try:
        with sqlite_util.connect(str(tmp_path / "order.db")):
            pass
    finally:
        monkey.undo()

    busy = next(i for i, s in enumerate(order) if "busy_timeout" in s)
    journal = next(i for i, s in enumerate(order) if "journal_mode" in s)
    assert busy < journal, order


def test_a_reader_does_not_fail_while_a_writer_holds_the_lock(tmp_path):
    """The failure this prevents: "database is locked" on a normal request."""
    path = str(tmp_path / "race.db")
    with sqlite_util.connect(path) as conn:
        conn.execute("CREATE TABLE t (x)")

    started = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []

    def writer():
        with sqlite_util.connect(path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO t VALUES (1)")
            started.set()
            release.wait(5)
            conn.commit()

    def reader():
        try:
            started.wait(5)
            with sqlite_util.connect(path) as conn:
                conn.execute("SELECT count(*) FROM t").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for t in threads:
        t.start()
    started.wait(5)
    threading.Event().wait(0.2)
    release.set()
    for t in threads:
        t.join(10)

    assert errors == [], f"a plain read failed under a write: {errors}"


def test_an_exception_rolls_back_and_still_closes(tmp_path):
    path = str(tmp_path / "rollback.db")
    with sqlite_util.connect(path) as conn:
        conn.execute("CREATE TABLE t (x)")

    with pytest.raises(RuntimeError):
        with sqlite_util.connect(path) as conn:
            conn.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("abort")

    with sqlite_util.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 0


def test_write_mode_takes_the_lock_before_reading(tmp_path):
    """BEGIN IMMEDIATE, so two writers serialise before either has read.

    Deferred transactions let both writers read the same pre-state and then
    collide on commit, which is the classic lost update.
    """
    path = str(tmp_path / "immediate.db")
    with sqlite_util.connect(path) as conn:
        conn.execute("CREATE TABLE t (x)")

    with sqlite_util.connect(path, write=True) as conn:
        conn.execute("INSERT INTO t VALUES (1)")
        # The write lock is already held, so a second BEGIN IMMEDIATE would
        # block until busy_timeout rather than succeed.
        assert _pragma(conn, "busy_timeout") == 10000

    with sqlite_util.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 1


def test_every_project_database_uses_the_shared_setup():
    """Guards against a sixth copy appearing with its own PRAGMA set."""
    import inspect

    from core import events, schedule_store, tracking
    from web.auth import db as auth_db
    from web.indexer import db as index_db

    for module in (events, tracking, auth_db, index_db):
        source = inspect.getsource(module)
        assert "sqlite_util.connect" in source, \
            f"{module.__name__} grew its own sqlite3.connect"
        # And no module should be setting pragmas on its own any more.
        assert "PRAGMA journal_mode" not in source, \
            f"{module.__name__} sets journal_mode itself; use core.sqlite_util"

    # schedule_store keeps its bespoke connect(write=...) but must not drift
    # on the settings that matter.
    src = inspect.getsource(schedule_store)
    assert "busy_timeout" in src and "journal_mode=WAL" in src
