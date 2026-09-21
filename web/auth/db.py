"""SQLite-backed user store."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Iterator, Optional

_DB_PATH_OVERRIDE: Optional[str] = None
_lock = threading.Lock()


def db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    from web.settings import get_settings
    return os.path.join(get_settings().paths.state_dir, "users.db")


def set_db_path(path: str):
    global _DB_PATH_OVERRIDE
    _DB_PATH_OVERRIDE = path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with _lock, connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin','editor','viewer')),
                created_at TEXT NOT NULL,
                last_login_at TEXT,
                disabled INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                jti TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('access','refresh')),
                expires_at INTEGER NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE TABLE IF NOT EXISTS api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                prefix TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                last_used_at TEXT,
                expires_at INTEGER,
                revoked INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id);
            """
        )


# ----- User operations -----


def create_user(username: str, password_hash: str, role: str) -> int:
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?,?,?,?)",
            (username, password_hash, role, now),
        )
        return cur.lastrowid


def get_user_by_username(username: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return dict(row) if row else None


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, role, created_at, last_login_at, disabled FROM users ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]


def update_last_login(user_id: int):
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id))


def delete_user(user_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


def update_password(user_id: int, password_hash: str):
    with connect() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def set_role(user_id: int, role: str):
    with connect() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def set_disabled(user_id: int, disabled: bool):
    with connect() as conn:
        conn.execute(
            "UPDATE users SET disabled = ? WHERE id = ?",
            (1 if disabled else 0, user_id),
        )


# ----- Session/JTI operations -----


def store_session(jti: str, user_id: int, kind: str, expires_at: int):
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sessions (jti, user_id, kind, expires_at, revoked) VALUES (?,?,?,?,0)",
            (jti, user_id, kind, expires_at),
        )


def is_revoked(jti: str) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT revoked FROM sessions WHERE jti = ?", (jti,)
        ).fetchone()
        return bool(row and row["revoked"])


def revoke_session(jti: str):
    with connect() as conn:
        conn.execute("UPDATE sessions SET revoked = 1 WHERE jti = ?", (jti,))


def revoke_user_sessions(user_id: int, except_jtis: Optional[list[str]] = None):
    """Revoke sessions for a user, optionally keeping some (e.g. current)."""
    with connect() as conn:
        if except_jtis:
            placeholders = ",".join("?" for _ in except_jtis)
            conn.execute(
                f"UPDATE sessions SET revoked = 1 WHERE user_id = ? AND jti NOT IN ({placeholders})",
                (user_id, *except_jtis),
            )
        else:
            conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ?", (user_id,))


def list_sessions(user_id: int, active_only: bool = True) -> list[dict]:
    """List sessions for a user (most recent expiry first)."""
    now_ts = int(time.time())
    query = "SELECT jti, user_id, kind, expires_at, revoked FROM sessions WHERE user_id = ?"
    params: list = [user_id]
    if active_only:
        query += " AND revoked = 0 AND expires_at > ?"
        params.append(now_ts)
    query += " ORDER BY expires_at DESC"
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def get_session(jti: str) -> Optional[dict]:
    with connect() as conn:
        row = conn.execute(
            "SELECT jti, user_id, kind, expires_at, revoked FROM sessions WHERE jti = ?",
            (jti,),
        ).fetchone()
        return dict(row) if row else None


def cleanup_expired(now_ts: Optional[int] = None):
    """Remove expired session rows."""
    now_ts = now_ts or int(time.time())
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_ts,))


# ----- API tokens (programmatic access) -----


def _hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_token(user_id: int, name: str,
                     expires_at: Optional[int] = None) -> tuple[int, str]:
    """Create an API token. Returns (id, plaintext_token) — shown only once."""
    import secrets

    token = "air_" + secrets.token_hex(24)
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO api_tokens (user_id, name, prefix, token_hash, created_at, expires_at)"
            " VALUES (?,?,?,?,?,?)",
            (user_id, name, token[:12], _hash_token(token), now, expires_at),
        )
        return cur.lastrowid, token


def list_api_tokens(user_id: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, prefix, created_at, last_used_at, expires_at, revoked"
            " FROM api_tokens WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def revoke_api_token(user_id: int, token_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked = 1 WHERE id = ? AND user_id = ?",
            (token_id, user_id),
        )
        return cur.rowcount > 0


def get_api_token(token: str) -> Optional[dict]:
    """Look up a valid (non-revoked, non-expired) token. Returns the row."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE token_hash = ?",
            (_hash_token(token),),
        ).fetchone()
    if not row:
        return None
    row = dict(row)
    if row.get("revoked"):
        return None
    if row.get("expires_at") and int(row["expires_at"]) < int(time.time()):
        return None
    return row


def touch_api_token(token_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
            (time.strftime("%Y-%m-%dT%H:%M:%S%z"), token_id),
        )
