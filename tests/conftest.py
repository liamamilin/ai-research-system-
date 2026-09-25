"""Shared pytest fixtures: isolated web settings, temp dirs, API clients."""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PASSWORDS = ("admin-pass-123", "editor-pass-123", "viewer-pass-123", "ghost-pass-123")

_WEB_YAML = """\
server:
  host: "127.0.0.1"
  port: 9999
auth:
  secret_key: "test-secret-key-for-unit-tests-0123456789"
  access_token_minutes: 15
  refresh_token_days: 7
  cookie_secure: false
  cookie_samesite: "lax"
cors:
  enabled: false
rate_limit:
  login_per_minute: 1000
  job_run_per_minute: 1000
paths:
  jobs_dir: "{jobs}"
  config_dir: "{config}"
  output_dir: "{output}"
  state_dir: "{state}"
  logs_dir: "{logs}"
"""


@pytest.fixture(scope="session")
def _password_hashes():
    from web.auth.password import hash_password

    return {pw: hash_password(pw) for pw in _PASSWORDS}


@pytest.fixture()
def web_env(tmp_path, monkeypatch, _password_hashes):
    """Isolated web settings + user DB in a tmp dir.

    Returns (tmp_path, users) where users maps username → {id, password, role}.
    """
    for name in ("jobs", "config", "output", "state", "logs"):
        (tmp_path / name).mkdir()

    cfg_path = tmp_path / "web.yaml"
    cfg_path.write_text(
        _WEB_YAML.format(
            jobs=tmp_path / "jobs",
            config=tmp_path / "config",
            output=tmp_path / "output",
            state=tmp_path / "state",
            logs=tmp_path / "logs",
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("AI_RESEARCH_WEB_CONFIG", str(cfg_path))
    monkeypatch.setenv("AI_RESEARCH_ENV", "development")
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)

    import web.settings as web_settings
    monkeypatch.setattr(web_settings, "_settings", None)

    from web.auth import db as user_db
    db_file = tmp_path / "state" / "users.db"
    monkeypatch.setattr(user_db, "_DB_PATH_OVERRIDE", str(db_file))
    user_db.init_db()

    from web.indexer import db as index_db
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE",
                        str(tmp_path / "state" / "reports.db"))
    index_db.init_db()

    # Never read or write the developer's real crontab from a test. Without
    # this, /api/health/detailed consults the live schedule: enabling the real
    # pipeline made the suite fail on the machine's own cron state.
    from web.services import scheduler as scheduler_service

    # launchctl would otherwise report this machine's real agents, so a test
    # would depend on whether the pipeline is currently installed.
    monkeypatch.setattr(scheduler_service, "list_launchd_agents", lambda: [])

    monkeypatch.setattr(
        scheduler_service, "_get_crontab",
        lambda: [
            "# test scheduler",
            "# cron_id: test_job",
            "# 0 6 * * * cd /tmp && bash scripts/run.sh",
        ],
    )

    # Keep usage/history writes inside the tmp state dir: the QA route records
    # LLM usage, and without this every test would append to the real
    # state/history/__usage__.jsonl.
    from core import state as core_state
    core_state.use_state_dir(str(tmp_path / "state"))

    users: dict[str, dict] = {}
    for username, role, password in (
        ("admin", "admin", "admin-pass-123"),
        ("editor", "editor", "editor-pass-123"),
        ("viewer", "viewer", "viewer-pass-123"),
        ("ghost", "viewer", "ghost-pass-123"),
    ):
        uid = user_db.create_user(username, _password_hashes[password], role)
        users[username] = {"id": uid, "password": password, "role": role}
    user_db.set_disabled(users["ghost"]["id"], True)

    return tmp_path, users


@pytest.fixture()
def app(web_env):
    from web.server import create_app

    return create_app()


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


# --- shared index-isolation fixture -----------------------------------------
# A watcher started without redirecting the index database writes its fixtures
# into the live reports.db, which then reports reports that do not exist on
# disk. Every test that starts a watcher goes through this fixture.

@pytest.fixture()
def watcher_env(tmp_path, monkeypatch):
    """Isolated output dir + reports DB, with watchers stopped before teardown.

    Depends on monkeypatch so this fixture is finalized *before* monkeypatch
    undoes the DB path override — otherwise a lingering watcher thread would
    write into the next test's database (or the real one).
    """
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    state = tmp_path / "state"
    output = tmp_path / "output"
    state.mkdir()
    output.mkdir()
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(state / "reports.db"))
    index_db.init_db()
    vector_sync.reset_client()
    monkeypatch.setattr(vector_sync, "_embedding_client", lambda: None)

    started: list[tuple[threading.Event, object]] = []
    out_dir = output
    state_dir = state

    class Env:
        output = out_dir
        state = state_dir

        @staticmethod
        def start(watcher, **kwargs):
            stop = threading.Event()
            kwargs.setdefault("state_dir", str(state_dir))
            thread = watcher.start_watcher(str(out_dir), stop_event=stop, **kwargs)
            started.append((stop, thread))
            return thread

        @staticmethod
        def stop():
            for stop, thread in started:
                stop.set()
            for _stop, thread in started:
                thread.join(timeout=5)
            started.clear()

    try:
        yield Env()
    finally:
        Env.stop()
        vector_sync.reset_client()


def write_report(output, rel, text="# Title\n\nbody about steam and indie games\n"):
    """Create a report file below ``output`` and return its path."""
    path = os.path.join(str(output), rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
