"""One SQLite connection setup, shared by every database in the project.

There were five ``connect()`` helpers and they had already drifted:

===========================  ==========  ==============  ============
module                      busy_timeout  journal_mode    synchronous
===========================  ==========  ==============  ============
core/schedule_store.py      yes          WAL             FULL
web/indexer/db.py           no           WAL             default
core/events.py              no           delete          default
web/auth/db.py              no           delete          default
core/tracking.py            no           delete          default
===========================  ==========  ==============  ============

The default ``busy_timeout`` is zero, so a read racing a write raises
``database is locked`` immediately rather than waiting. That is not a
theoretical risk here: the Web console is multi-threaded, ``users.db`` is read
on every authenticated request and written on every login and every token
refresh, and the pipeline writes ``events.db`` from six stages at once.

The order below matters and is not cosmetic. Switching the journal mode needs a
brief exclusive lock, and a connection that has not yet installed a busy
timeout fails instantly instead of waiting for it -- so two connections
switching at the same time means one of them raises. Set the timeout first.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional

#: Durable but not paranoid. ``FULL`` fsyncs on every commit; for the scheduler's
#: own bookkeeping that is the right trade, for a report index read on every page
#: it is not.
DEFAULT_SYNCHRONOUS = "NORMAL"
DEFAULT_TIMEOUT = 10.0


def open_db(
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    synchronous: str = DEFAULT_SYNCHRONOUS,
    create_parents: bool = True,
) -> sqlite3.Connection:
    """Open a connection with this project's standard PRAGMAs.

    Callers own the connection and must close it. Prefer :func:`connect` unless
    you need the handle to outlive one ``with`` block.
    """
    if create_parents:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    # Before the journal switch, so a concurrent switcher waits instead of
    # raising. See the module docstring.
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        # The mode is a property of the file, not the connection, so whoever
        # lost the race will see it on their next connect. Failing here would
        # turn a harmless startup race into an outage.
        pass
    conn.execute(f"PRAGMA synchronous={synchronous}")
    return conn


@contextmanager
def connect(
    path: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    synchronous: str = DEFAULT_SYNCHRONOUS,
    create_parents: bool = True,
    write: bool = False,
) -> Iterator[sqlite3.Connection]:
    """Transactional connection: commits on success, rolls back on error."""
    conn = open_db(
        path, timeout=timeout, synchronous=synchronous,
        create_parents=create_parents,
    )
    try:
        if write:
            # Take the write lock up front so two writers serialise here rather
            # than half-way through, after one has already read stale rows.
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def is_wal(path: str) -> Optional[bool]:
    """Whether ``path`` is currently in WAL mode; None if it cannot be read."""
    try:
        conn = sqlite3.connect(path, timeout=1.0)
    except sqlite3.Error:
        return None
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    except sqlite3.Error:
        return None
    finally:
        conn.close()
