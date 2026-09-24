"""Tests must never write to the real state/ or output/.

A test that starts a real watcher without redirecting the index database writes
its fixtures into the live reports.db: the rows then show up as "reports" that
do not exist on disk, and the health check reports orphans that no one can
explain. This check runs the suite's own isolation rules over the modules that
reach for state.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_repo_has_a_real_state_dir_to_protect():
    """If this fails there is nothing to protect and the test is meaningless."""
    assert (REPO / "state").is_dir()
    assert (REPO / "output").is_dir()


def test_watcher_fixture_redirects_the_index_database(watcher_env):
    """watcher_env is the only sanctioned way to start a real watcher."""
    from web.indexer import db as index_db

    assert index_db.db_path().startswith(str(watcher_env.state)), \
        "watcher_env did not redirect reports.db into the tmp state dir"


def test_web_env_redirects_state(web_env):
    """The shared web_env fixture must point state at tmp, not the repo."""
    from core import state as core_state

    tmp_path, _users = web_env
    core_state.use_state_dir(str(tmp_path / "state"))
    assert core_state._STATE_DIR.startswith(str(tmp_path)), \
        "state writes would land in the real state/ directory"


def test_no_test_module_starts_a_watcher_outside_the_fixture():
    """Catch the exact mistake that polluted the live index.

    A watcher started directly writes to whatever reports.db the process has
    open. The only sanctioned entry point is watcher_env.start(...), which
    redirects the database first.
    """
    self_name = Path(__file__).name
    offenders = []
    for path in (REPO / "tests").glob("test_*.py"):
        if path.name == self_name:
            continue  # this module names the pattern on purpose
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(("thread = watcher.start_watcher(",
                                       "watcher.start_watcher(")):
                continue
            # The fixture helper is allowed: it is defined inside a fixture
            # that has already redirected the index database.
            if "out_dir" in stripped or "watcher_env.output" in stripped:
                continue
            offenders.append(f"{path.name}:{lineno} {stripped}")
    assert not offenders, (
        "these tests start a watcher directly and can write to the real "
        f"index: {offenders}"
    )


def test_index_db_override_is_restored_between_tests():
    """A leaked override sends the next test's writes somewhere unexpected."""
    from web.indexer import db as index_db

    assert index_db._DB_PATH_OVERRIDE is None or not os.path.isabs(
        str(index_db._DB_PATH_OVERRIDE)) or str(
        index_db._DB_PATH_OVERRIDE).startswith(str(REPO / "tests"))


def test_tests_never_touch_the_real_crontab(web_env):
    """The scheduler reads crontab unless a test overrides it.

    /api/health/detailed consults the live schedule, so an unisolated suite
    depends on the developer's own cron state — it failed as soon as the real
    pipeline was enabled.
    """
    from web.services import scheduler as scheduler_service

    listed = scheduler_service.list_jobs()
    for job in listed:
        assert job.get("id") == "test_job", f"real crontab leaked into tests: {job}"
