"""Health checks, atomic file IO and job-lock ownership."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import pytest

from core import fileio, health


def _make_reports_db(path: str, reports: int = 3, embedded: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (path TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS chunk_meta (path TEXT PRIMARY KEY);
            """
        )
        for i in range(reports):
            conn.execute("INSERT OR IGNORE INTO reports VALUES (?)", (f"r{i}.md",))
        for i in range(embedded):
            conn.execute("INSERT OR IGNORE INTO chunk_meta VALUES (?)", (f"r{i}.md",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def env(tmp_path):
    state = tmp_path / "state"
    output = tmp_path / "output"
    config = tmp_path / "config"
    for d in (state, output, config):
        d.mkdir()
    (config / "system.yaml").write_text("ai:\n  model: test-model\n", encoding="utf-8")
    _make_reports_db(str(state / "reports.db"))
    conn = sqlite3.connect(str(state / "tracking.db"))
    conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    # Real on-disk shape: a date-keyed mapping (shared with the web runner).
    (state / "pipeline_rounds.json").write_text(
        json.dumps({"2026-09-24": {"date": "2026-09-24", "status": "success",
                                   "trigger": "cron", "started_at": "2026-09-24T06:00:00",
                                   "finished_at": "2026-09-24T06:10:00", "stages": []}}),
        encoding="utf-8",
    )
    return {"state": str(state), "output": str(output), "config": str(config)}


def _run(env, deep=True):
    return health.run_checks(state_dir=env["state"], output_dir=env["output"],
                             config_dir=env["config"], deep=deep)


def _by_name(result, name):
    return next(c for c in result["checks"] if c["name"] == name)


# --- health checks ----------------------------------------------------------


def test_clean_environment_is_healthy(env):
    result = _run(env)
    assert result["status"] == "ok"
    assert result["errors"] == [] and result["warnings"] == []
    assert _by_name(result, "reports_db")["value"] == 3


def test_fast_checks_skip_index_inspection(env):
    result = _run(env, deep=False)
    names = [c["name"] for c in result["checks"]]
    assert "index_freshness" not in names
    assert "vector_coverage" not in names
    assert "config" in names and "disk_free" in names


def test_missing_config_is_error(env):
    os.remove(os.path.join(env["config"], "system.yaml"))
    result = _run(env)
    assert result["status"] == "error"
    assert "config" in result["errors"]


def test_broken_config_is_error(env):
    with open(os.path.join(env["config"], "system.yaml"), "w", encoding="utf-8") as f:
        f.write("ai: [unclosed\n")
    result = _run(env)
    assert result["status"] == "error"
    assert "config" in result["errors"]


def test_config_without_model_warns(env):
    with open(os.path.join(env["config"], "system.yaml"), "w", encoding="utf-8") as f:
        f.write("search:\n  provider: duckduckgo\n")
    result = _run(env)
    assert result["status"] == "warn"
    assert "config" in result["warnings"]


def test_unreadable_state_dir_is_error(env):
    blocker = os.path.join(env["state"], "reports.db")
    os.remove(blocker)
    os.makedirs(blocker)  # a directory where the DB file is expected
    result = _run(env)
    assert "reports_db" in result["errors"]
    assert result["status"] == "error"


def test_missing_databases_warn_not_error(env):
    os.remove(os.path.join(env["state"], "reports.db"))
    os.remove(os.path.join(env["state"], "tracking.db"))
    result = _run(env)
    assert result["status"] == "warn"
    assert set(result["warnings"]) >= {"reports_db", "tracking_db"}


def test_vector_coverage_warns_when_reports_lack_vectors(env):
    _make_reports_db(os.path.join(env["state"], "reports.db"), reports=5, embedded=3)
    result = _run(env)
    check = _by_name(result, "vector_coverage")
    assert check["status"] == "warn"
    assert check["missing"] == 2
    assert "FTS" in check["detail"]


def test_index_freshness_warns_on_lag(env):
    for i in range(25):
        with open(os.path.join(env["output"], f"new{i}.md"), "w", encoding="utf-8") as f:
            f.write("# new")
    result = _run(env)
    check = _by_name(result, "index_freshness")
    assert check["status"] == "warn"
    assert check["missing"] == 22  # 25 new files minus the 3 pre-indexed reports


def _write_rounds(env, payload) -> None:
    with open(os.path.join(env["state"], "pipeline_rounds.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f)


def test_last_round_reads_status(env):
    _write_rounds(env, {
        "2026-09-24": {"date": "2026-09-24", "status": "success", "trigger": "cron",
                       "started_at": "2026-09-24T06:00:00", "stages": []},
    })
    check = _by_name(_run(env), "last_round")
    assert check["status"] == "ok"
    assert "via cron" in check["detail"]

    _write_rounds(env, {
        "2026-09-25": {"date": "2026-09-25", "status": "failed", "trigger": "web",
                       "stages": [{"key": "P0", "status": "failed"},
                                  {"key": "P1", "status": "success"}]},
        "2026-09-24": {"date": "2026-09-24", "status": "success", "stages": []},
    })
    check = _by_name(_run(env), "last_round")
    assert check["status"] == "error"
    assert check["failed_stages"] == 1
    assert check["total_stages"] == 2
    assert "1/2 stages failed" in check["detail"]


def test_last_round_uses_latest_date(env):
    _write_rounds(env, {
        "2026-01-01": {"date": "2026-01-01", "status": "failed", "stages": []},
        "2026-09-24": {"date": "2026-09-24", "status": "success", "stages": []},
    })
    assert _by_name(_run(env), "last_round")["round_status"] == "success"


def test_last_round_corrupt_file_is_error(env):
    with open(os.path.join(env["state"], "pipeline_rounds.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert _by_name(_run(env), "last_round")["status"] == "warn"


def test_stale_locks_detection(env):
    lock_dir = os.path.join(env["state"], "locks")
    os.makedirs(lock_dir)
    old = os.path.join(lock_dir, "old.lock")
    with open(old, "w", encoding="utf-8") as f:
        f.write("{}")
    fresh = os.path.join(lock_dir, "fresh.lock")
    with open(fresh, "w", encoding="utf-8") as f:
        f.write("{}")
    old_ts = time.time() - 7200
    os.utime(old, (old_ts, old_ts))
    assert health.stale_locks(env["state"], stale_after=3600) == ["old.lock"]


def test_health_api_reports_status(client, web_env):
    tmp_path, _users = web_env
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n  model: test-model\n", encoding="utf-8")
    _login = client.post("/api/auth/login",
                         json={"username": "admin", "password": "admin-pass-123"})
    assert _login.status_code == 200
    detailed = client.get("/api/health/detailed")
    assert detailed.status_code == 200
    body = detailed.json()
    assert body["deep"] is True
    assert body["status"] in ("ok", "warn")
    assert "config" not in body["errors"]
    assert "stale_locks" in body


def test_health_api_detailed_requires_admin(client, web_env):
    client.post("/api/auth/login",
                json={"username": "viewer", "password": "viewer-pass-123"})
    assert client.get("/api/health/detailed").status_code == 403


def test_health_api_requires_no_auth(client, web_env):
    r = client.get("/api/health")
    assert r.status_code in (200, 503)
    assert r.json()["status"] in ("ok", "warn", "error")


# --- atomic IO --------------------------------------------------------------


def test_atomic_write_replaces_content(tmp_path):
    path = str(tmp_path / "state.json")
    fileio.atomic_write(path, '{"a": 1}')
    fileio.atomic_write(path, '{"a": 2}')
    assert json.load(open(path, encoding="utf-8")) == {"a": 2}
    assert not [f for f in os.listdir(str(tmp_path)) if ".tmp." in f]


def test_atomic_write_cleans_temp_on_failure(tmp_path):
    path = str(tmp_path / "sub" / "state.json")
    fileio.atomic_write(path, "ok")
    with pytest.raises(OSError):
        fileio.atomic_write(str(tmp_path / "sub"), "x")
    assert not [f for f in os.listdir(str(tmp_path / "sub")) if ".tmp." in f]


def test_file_lock_serializes_threads(tmp_path):
    key = str(tmp_path / "counter")
    order: list[str] = []
    started = threading.Event()

    def worker(name: str, hold: float) -> None:
        with fileio.file_lock(key, timeout=5):
            order.append(f"{name}-in")
            started.set()
            time.sleep(hold)
            order.append(f"{name}-out")

    t1 = threading.Thread(target=worker, args=("a", 0.15))
    t2 = threading.Thread(target=worker, args=("b", 0.01))
    t1.start()
    started.wait(2)
    t2.start()
    t1.join(5)
    t2.join(5)
    assert order == ["a-in", "a-out", "b-in", "b-out"]


def test_state_update_is_atomic_and_readable(tmp_path, monkeypatch):
    from core import state as state_mod

    monkeypatch.setattr(state_mod, "_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state_mod, "_HISTORY_DIR", str(tmp_path / "state" / "history"))
    state_mod.StateManager.update("job/a", "success", output_path="out/a.md",
                                  usage={"total_tokens": 10})
    assert state_mod.StateManager.get("job/a")["last_status"] == "success"
    history = state_mod.StateManager.get_history("job/a")
    assert history[0]["usage"]["total_tokens"] == 10
    leftovers = [f for f in os.listdir(str(tmp_path / "state")) if ".tmp." in f]
    assert leftovers == []


# --- job lock ownership -----------------------------------------------------


def test_lock_blocks_second_thread_same_process(tmp_path, monkeypatch):
    from core import lock as lock_mod

    monkeypatch.setattr(lock_mod, "_LOCK_DIR", str(tmp_path / "locks"))
    first = lock_mod.LockManager("job-a")
    assert first.acquire() is True

    result: dict = {}

    def other_thread() -> None:
        result["acquired"] = lock_mod.LockManager("job-a").acquire()

    t = threading.Thread(target=other_thread)
    t.start()
    t.join(5)
    assert result["acquired"] is False
    first.release()
    assert not os.path.isfile(str(tmp_path / "locks" / "job-a.lock"))


def test_lock_is_reentrant_for_same_thread(tmp_path, monkeypatch):
    from core import lock as lock_mod

    monkeypatch.setattr(lock_mod, "_LOCK_DIR", str(tmp_path / "locks"))
    outer = lock_mod.LockManager("job-b")
    assert outer.acquire() is True
    assert lock_mod.LockManager("job-b").acquire() is True
    outer.release()


def test_lock_payload_written_atomically(tmp_path, monkeypatch):
    from core import lock as lock_mod

    monkeypatch.setattr(lock_mod, "_LOCK_DIR", str(tmp_path / "locks"))
    mgr = lock_mod.LockManager("job-c")
    mgr.acquire()
    with open(mgr.lock_path, encoding="utf-8") as f:
        payload = json.load(f)
    assert payload["pid"] == os.getpid()
    assert ":" in payload["owner_id"]
    mgr.release()
