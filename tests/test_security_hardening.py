"""Security hardening: JWT secret hygiene and path containment."""

from __future__ import annotations

import os

import pytest


# --- JWT secret hygiene -----------------------------------------------------


def test_weak_secret_detection():
    from web.settings import is_weak_secret

    assert is_weak_secret("") is True
    assert is_weak_secret("CHANGE_ME") is True
    assert is_weak_secret("dev-only-change-me-in-production-aaaaaaaaaaaaaaaaaa") is True
    assert is_weak_secret("short") is True
    assert is_weak_secret("a" * 64) is True  # low entropy padding
    assert is_weak_secret("k3Jq8xTz2mNp5vRc7yBw9dF4hLs6uQaX1zCe0Gt5kIh8") is False


def test_generate_secret_is_strong_and_unique():
    from web.settings import generate_secret, is_weak_secret

    a, b = generate_secret(), generate_secret()
    assert a != b
    assert is_weak_secret(a) is False
    assert len(a) >= 48


def test_weak_secret_is_replaced_and_persisted(tmp_path, monkeypatch):
    """A placeholder secret must never survive startup."""
    from web import settings as ws

    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=\"x\"\nWEB_SECRET_KEY=dev-only-change-me-in-production-aaaaaaaaaaaaaaaaaa\n",
                        encoding="utf-8")
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)
    monkeypatch.setattr(ws, "_persist_secret", lambda secret: (
        env_file.write_text(f"AI_RESEARCH_SECRET_KEY={secret}\n", encoding="utf-8") or str(env_file)
    ))

    config = tmp_path / "web.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 8765\n"
        "auth:\n  secret_key: \"dev-only-change-me-in-production-aaaaaaaaaaaaaaaaaa\"\n"
        f"paths:\n  jobs_dir: {tmp_path / 'jobs'}\n  config_dir: {tmp_path / 'config'}\n"
        f"  output_dir: {tmp_path / 'output'}\n  state_dir: {tmp_path / 'state'}\n"
        f"  logs_dir: {tmp_path / 'logs'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_RESEARCH_WEB_CONFIG", str(config))
    monkeypatch.setenv("AI_RESEARCH_ENV", "development")
    monkeypatch.setattr(ws, "_settings", None)

    loaded = ws.get_settings()
    assert ws.is_weak_secret(loaded.auth.secret_key) is False
    assert "AI_RESEARCH_SECRET_KEY=" in env_file.read_text(encoding="utf-8")


def test_strong_env_secret_is_preserved(tmp_path, monkeypatch):
    from web import settings as ws

    strong = "k3Jq8xTz2mNp5vRc7yBw9dF4hLs6uQaX1zCe0Gt5kIh8"
    config = tmp_path / "web.yaml"
    config.write_text(
        "server:\n  host: 127.0.0.1\n  port: 8765\n"
        f"auth:\n  secret_key: \"{strong}\"\n"
        f"paths:\n  jobs_dir: {tmp_path / 'jobs'}\n  config_dir: {tmp_path / 'config'}\n"
        f"  output_dir: {tmp_path / 'output'}\n  state_dir: {tmp_path / 'state'}\n"
        f"  logs_dir: {tmp_path / 'logs'}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_RESEARCH_WEB_CONFIG", str(config))
    monkeypatch.setenv("AI_RESEARCH_ENV", "development")
    monkeypatch.setenv("AI_RESEARCH_SECRET_KEY", strong)
    monkeypatch.setattr(ws, "_settings", None)

    loaded = ws.get_settings()
    assert loaded.auth.secret_key == strong


# --- job name path traversal ------------------------------------------------


@pytest.fixture()
def jobs_dir(tmp_path):
    root = tmp_path / "jobs"
    (root / "research").mkdir(parents=True)
    (root / "research" / "a.yaml").write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    outside = tmp_path / "outside.yaml"
    outside.write_text('name: "Outside"\nprompt: "secret"\n', encoding="utf-8")
    return str(root)


@pytest.mark.parametrize("bad", [
    "../outside",
    "../../etc/passwd",
    "/etc/passwd",
    "research/../../outside",
    "..",
])
def test_unsafe_job_names_are_rejected(jobs_dir, bad):
    from core.config import load_job

    assert load_job(jobs_dir, bad) is None


def test_normal_job_names_still_resolve(jobs_dir):
    from core.config import load_job

    assert load_job(jobs_dir, "research/a")["name"] == "A"
    assert load_job(jobs_dir, "a")["name"] == "A"


# --- output path containment ------------------------------------------------


def _engine(tmp_path, jobs_dir=None):
    from core.engine import ResearchEngine

    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "output").mkdir(exist_ok=True)
    return ResearchEngine(
        config_dir=str(tmp_path / "config"),
        jobs_dir=jobs_dir or str(tmp_path / "jobs"),
        workspace_dir=str(tmp_path),
    )


def test_output_inside_root_is_allowed(tmp_path):
    engine = _engine(tmp_path)
    path = engine._resolve_output_path({"name": "x", "output": "output/research/{date}_{name}.md"})
    assert path.endswith(".md")


def test_output_escaping_root_is_rejected(tmp_path):
    engine = _engine(tmp_path)
    for bad in ("../config/system.yaml", "../../etc/passwd", "/tmp/evil.md",
                "output/../../escape.md", "config/system.yaml"):
        with pytest.raises(ValueError, match="escapes the allowed root"):
            engine._resolve_output_path({"name": "x", "output": bad})


def test_output_root_can_be_overridden_explicitly(tmp_path):
    engine = _engine(tmp_path)
    custom = tmp_path / "reports"
    custom.mkdir()
    path = engine._resolve_output_path({
        "name": "x",
        "runtime": {"output_root": str(custom)},
        "output": "custom/{date}.md",
    })
    assert "custom" in path


def test_default_output_stays_relative(tmp_path):
    engine = _engine(tmp_path)
    path = engine._resolve_output_path({"name": "my job"})
    assert not os.path.isabs(path)
    assert path.startswith("output/")


# --- yaml output validation -------------------------------------------------


def test_validate_output_path_blocks_escapes():
    from web.services.yaml_io import validate_output_path

    assert validate_output_path({"output": "../x.md"}, output_root="output")
    assert validate_output_path({"output": "/etc/x.md"}, output_root="output")
    assert validate_output_path({"output": "output/x.txt"}, output_root="output")
    assert validate_output_path({"output": "output/{bogus}.md"}, output_root="output")
    assert validate_output_path({"output": "research/{date}_{name}.md"}, output_root="output") == []


# --- API-level guards -------------------------------------------------------


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def test_job_yaml_update_rejects_output_escape(client, web_env):
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    (jobs / "research").mkdir(parents=True, exist_ok=True)
    (jobs / "research" / "a.yaml").write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    _login(client)

    r = client.put(
        "/api/jobs/research/a",
        json={"yaml_content": 'name: "A"\nprompt: "x"\noutput: "../../evil.md"\n'},
        headers=_csrf(client),
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "invalid_output"


def test_job_yaml_update_accepts_safe_output(client, web_env):
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    (jobs / "research").mkdir(parents=True, exist_ok=True)
    (jobs / "research" / "a.yaml").write_text('name: "A"\nprompt: "x"\n', encoding="utf-8")
    _login(client)

    r = client.put(
        "/api/jobs/research/a",
        json={"yaml_content": 'name: "A"\nprompt: "x"\noutput: "output/research/{date}_{name}.md"\n'},
        headers=_csrf(client),
    )
    assert r.status_code == 200, r.text


def test_job_detail_rejects_traversal_name(client, web_env):
    tmp_path, _ = web_env
    jobs = tmp_path / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    (tmp_path / "outside.yaml").write_text('name: "Outside"\nprompt: "x"\n', encoding="utf-8")
    _login(client)

    for suffix in ("", "/yaml"):
        r = client.get(f"/api/jobs/..%2Foutside{suffix}")
        assert r.status_code in (404, 400), f"{suffix} -> {r.status_code}"


def test_round_diff_rejects_non_date(client, web_env):
    _login(client)
    for bad in ("..%2F..%2Fetc", "2026-9-1", "abcd-ef-gh"):
        r = client.get(f"/api/pipeline/rounds/{bad}/diff")
        assert r.status_code in (422, 404), f"{bad} -> {r.status_code}"


def test_round_diff_against_rejects_non_date(client, web_env):
    _login(client)
    r = client.get("/api/pipeline/rounds/2026-09-24/diff?against=..%2F..%2Fetc")
    assert r.status_code in (422, 404)
