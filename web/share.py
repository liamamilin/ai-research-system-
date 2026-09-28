"""Signed read-only share tokens for reports, and a way to take them back.

A share token is a self-contained JWT, which is what makes it work without a
database lookup -- and what made it impossible to withdraw. Once a link was
handed to somebody it stayed live for up to 90 days, and the only way to kill
it was to rotate ``WEB_SECRET_KEY``, which signs you out of the console and
invalidates every other share link at the same time.

So links are recorded when they are created. That serves two purposes: the token
carries a ``jti`` that can be revoked, and the operator can see what is
currently exposed. The registry is advisory for listing and authoritative for
revocation -- ``verify_share_token`` consults it on every read.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from typing import Optional

import jwt

from core.fileio import atomic_write, file_lock
from web.settings import get_settings

_ALG = "HS256"
SCOPE = "report-share"
MAX_TTL_HOURS = 24 * 90
DEFAULT_TTL_HOURS = 168  # 7 days

#: Revoked and live links alike are kept until well past their expiry, so the
#: list can explain why an old link stopped working. Reap on write.
_KEEP_AFTER_EXPIRY_SECONDS = 30 * 86400
_MAX_ENTRIES = 500

_lock = threading.RLock()


def _registry_path() -> str:
    return os.path.join(get_settings().paths.state_dir, "share_links.json")


def _load() -> list[dict]:
    try:
        with open(_registry_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save(entries: list[dict]) -> None:
    path = _registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write(path, json.dumps(entries, ensure_ascii=False, indent=2))


def _record(entry: dict) -> None:
    with _lock, file_lock(_registry_path()):
        entries = _load()
        entries = [e for e in entries if e.get("jti") != entry.get("jti")]
        entries.append(entry)
        now = time.time()
        entries = [e for e in entries
                   if float(e.get("expires_at") or 0) + _KEEP_AFTER_EXPIRY_SECONDS > now]
        _save(entries[-_MAX_ENTRIES:])


def create_share_token(path: str, ttl_hours: int = DEFAULT_TTL_HOURS,
                       created_by: str = "") -> tuple[str, int, str]:
    """Return (token, expires_at, jti).

    The jti is returned as well as embedded, because the caller has to show it
    in a list where a revoked link can be pointed at again.
    """
    settings = get_settings()
    ttl_hours = max(1, min(int(ttl_hours), MAX_TTL_HOURS))
    now = int(time.time())
    exp = now + ttl_hours * 3600
    jti = secrets.token_urlsafe(9)
    payload = {
        "scope": SCOPE,
        "path": path,
        "by": created_by,
        "jti": jti,
        "iat": now,
        "exp": exp,
    }
    token = jwt.encode(payload, settings.auth.secret_key, algorithm=_ALG)
    _record({"jti": jti, "path": path, "by": created_by,
             "created_at": now, "expires_at": exp, "revoked_at": None})
    return token, exp, jti


def is_revoked(jti: str) -> bool:
    if not jti:
        # A token minted before jti existed. It is still valid until it
        # expires, which is the pre-existing behaviour; refusing it would
        # break links that are currently working.
        return False
    return any(e.get("jti") == jti and e.get("revoked_at")
               for e in _load())


def revoke(jti: str) -> bool:
    """Revoke one link. Returns True if it was live and is now not."""
    with _lock, file_lock(_registry_path()):
        entries = _load()
        hit = False
        for entry in entries:
            if entry.get("jti") == jti and not entry.get("revoked_at"):
                entry["revoked_at"] = int(time.time())
                hit = True
        if hit:
            _save(entries)
        return hit


def list_links(include_expired: bool = False) -> list[dict]:
    """Every link this app has issued, newest first.

    ``state`` is computed here rather than stored, so a link that expired on
    its own is not reported as if someone had withdrawn it.
    """
    now = time.time()
    out = []
    for entry in _load():
        expired = float(entry.get("expires_at") or 0) <= now
        revoked = bool(entry.get("revoked_at"))
        if not include_expired and expired and not revoked:
            continue
        row = dict(entry)
        row["expired"] = expired
        row["state"] = "revoked" if revoked else ("expired" if expired else "live")
        out.append(row)
    return sorted(out, key=lambda e: int(e.get("created_at") or 0), reverse=True)


def verify_share_token(token: str) -> Optional[dict]:
    """Return the payload of a valid, unrevoked share token, else None."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.auth.secret_key, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if payload.get("scope") != SCOPE:
        return None
    if is_revoked(payload.get("jti") or ""):
        return None
    return payload
