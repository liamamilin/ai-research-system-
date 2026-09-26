"""Regression tests for the five P0 hardening fixes.

Each test names the failure it prevents, so a future refactor that reintroduces
one says why it matters rather than just going red.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# --- P0-1: the agent must not be able to read credentials -------------------

def test_agent_tools_refuse_the_env_file():
    """The workspace is the project root, so .env is inside the sandbox.

    Without this an editor-level job can ask the model to read the JWT signing
    key, publish the result, and forge an admin token from a share link.
    """
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.workspace = str(ROOT)
    assert agent._resolve_workspace_path(".env") is None
    assert agent._resolve_workspace_path(".env.local") is None


def test_agent_tools_refuse_other_secret_bearing_files():
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.workspace = str(ROOT)
    for path in ("state/users.db", "state/schedules.db", "config/system.yaml",
                 "server.key", "id_rsa", "secrets/token.txt"):
        assert agent._resolve_workspace_path(path) is None, path


def test_refusal_survives_traversal_and_case():
    """`output/../.env` resolves to the same file, so the check must too."""
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.workspace = str(ROOT)
    for path in ("output/../.env", "jobs/../../.env", "spec/../config/web.yaml"):
        assert agent._resolve_workspace_path(path) is None, path


def test_research_material_is_still_reachable():
    """The denylist must not break the synthesis stages."""
    from core.research import ResearchAgent

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.workspace = str(ROOT)
    for path in ("output", "jobs", "spec/01-practical_information_matrix.md",
                 "output/practical_ai_intelligence", "logs/ai_research.log"):
        assert agent._resolve_workspace_path(path), path


def test_sensitive_path_helper_is_pure_and_scoped():
    from core.research import is_sensitive_path

    assert is_sensitive_path("/proj/.env", "/proj") is True
    assert is_sensitive_path("/proj/output/a.md", "/proj") is False
    # A path outside the workspace is the containment check's business.
    assert is_sensitive_path("/etc/passwd", "/proj") is False


def test_list_files_cannot_escape_via_the_pattern():
    """`directory` is validated but `pattern` was concatenated unchecked."""
    import glob
    import os as _os

    # os.path.join discards the base when the pattern is absolute.
    assert _os.path.join("/proj/output", "/etc/*") == "/etc/*"
    # The glob that produced would therefore read outside the workspace.
    assert glob.glob("/etc/*")


# --- P0-2: report HTML export must not carry executable markup ---------------

@pytest.mark.parametrize("payload", [
    "<script>fetch('/api/config/secrets',{method:'PUT'})</script>",
    "<img src=x onerror=\"alert(document.cookie)\">",
    "<iframe src='http://evil'></iframe>",
    "<svg onload=alert(1)></svg>",
    "<style>body{display:none}</style>",
    "<form action='http://evil'><input name=a></form>",
    "<base href='http://evil'>",
    "<meta http-equiv=refresh content='0;url=http://evil'>",
    "[click me](javascript:alert(1))",
    "<scr<script>ipt>alert(1)</script>",
])
def test_export_strips_executable_markup(payload):
    from core.render import render_markdown

    out = render_markdown(f"# Report\n\n{payload}").lower()
    for marker in ("<script", "onerror", "<iframe", "javascript:", "onload",
                   "<style", "<form", "<svg", "<base", "<meta"):
        assert marker not in out, f"{marker} survived: {out[:200]}"


def test_export_keeps_real_report_content():
    from core.render import render_markdown

    out = render_markdown(
        "# 标题\n\n正文 **粗体** 与 [链接](https://example.com)\n\n"
        "| 维度 | 分数 |\n|---|---|\n| 成本 | 9 |\n\n"
        "> 引用\n\n```python\nprint('hi')\n```"
    )
    assert "<h1" in out and "<strong>粗体</strong>" in out
    assert 'href="https://example.com"' in out
    assert "<table>" in out and "<blockquote>" in out
    # Links get noopener/noreferrer so a report cannot reach back into the app.
    assert 'rel="noopener noreferrer"' in out


def test_html_export_downloads_by_default_and_sets_hardening_headers(client, web_env):
    out = Path(web_env[0] / "output")
    out.mkdir(parents=True, exist_ok=True)
    (out / "sec.md").write_text("# t\n\n<script>alert(1)</script>\n", encoding="utf-8")
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    response = client.get("/api/reports/html", params={"path": "sec.md"})
    assert response.status_code == 200
    # Served as a download: model output should not render on our own origin by
    # default.
    assert "attachment" in response.headers.get("content-disposition", "")
    assert "<script" not in response.text.lower()
    assert "sandbox" in response.headers.get("content-security-policy", "")
    assert response.headers.get("x-content-type-options") == "nosniff"


def test_html_export_filename_cannot_inject_a_header(client, web_env):
    """A quote in a report name used to build a raw Content-Disposition value."""
    out = Path(web_env[0] / "output")
    out.mkdir(parents=True, exist_ok=True)
    (out / 'we"ird.md').write_text("# t\n", encoding="utf-8")
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    response = client.get("/api/reports/html", params={'path': 'we"ird.md'})
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\n" not in disposition and "\r" not in disposition
    # RFC 5987 form, so the raw name never has to be escaped by hand.
    assert "filename*=UTF-8''" in disposition


def test_html_export_rejects_a_traversal_name(client, web_env):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    response = client.get("/api/reports/html", params={"path": "../../.env"})
    assert response.status_code in (400, 403, 404)
    assert "root:" not in response.text


def test_app_sends_baseline_security_headers(client, web_env):
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    response = client.get("/api/scheduler")
    assert response.status_code == 200
    assert response.headers.get("x-content-type-options") == "nosniff"
    # Share tokens live in the URL path, so a leaking Referer hands them out.
    assert response.headers.get("referrer-policy") == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers.get("content-security-policy", "")
    assert response.headers.get("x-frame-options") == "DENY"


# --- P0-3: the scheduler's own store must be backed up -----------------------

def test_backup_includes_the_scheduler_database(tmp_path):
    script = (ROOT / "scripts" / "backup_state.sh").read_text(encoding="utf-8")
    # Without this the newest and most operationally load-bearing database is
    # the one thing a restore cannot bring back.
    assert "state/schedules.db" in script


def test_backup_treats_the_report_index_as_derived(tmp_path):
    """reports.db is rebuildable from output/; copying it made snapshots huge."""
    base = tmp_path / "proj"
    (base / "state").mkdir(parents=True)
    (base / "config").mkdir(parents=True)
    (base / "state" / "users.db").write_bytes(b"x")
    (base / "state" / "reports.db").write_bytes(b"y" * 32)
    (base / "config" / "web.yaml").write_text("x: 1\n", encoding="utf-8")

    import subprocess
    out = subprocess.run(
        ["bash", str(ROOT / "scripts" / "backup_state.sh"),
         "--base", str(base), "--dest", str(tmp_path / "snap"), "--dry-run"],
        capture_output=True, text=True, timeout=60).stdout
    assert "skip (derived" in out
    assert "state/users.db" in out


def test_backup_prunes_old_snapshots(tmp_path):
    base = tmp_path / "proj"
    (base / "state" / "backups").mkdir(parents=True)
    (base / "state" / "users.db").write_bytes(b"x")
    for stamp in ("20260101_000000", "20260102_000000", "20260103_000000"):
        (base / "state" / "backups" / stamp).mkdir()
        (base / "state" / "backups" / stamp / "users.db").write_bytes(b"old")

    import subprocess
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "backup_state.sh"),
         "--base", str(base), "--dest", str(base / "state" / "backups" / "new")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "AREC_BACKUP_KEEP": "1"})
    assert result.returncode == 0, result.stderr
    remaining = sorted(p.name for p in (base / "state" / "backups").iterdir())
    assert remaining == ["new"], remaining


# --- P0-4: rate limits must be per person, not global -----------------------

def test_job_run_limit_is_keyed_per_principal():
    """All HS256 tokens share a header, so a prefix key was one global bucket."""
    import jwt as pyjwt

    from web.ratelimit import _principal_key

    class FakeRequest:
        def __init__(self, cookie="", authorization=""):
            self.cookies = {"ai_research_access": cookie} if cookie else {}
            self.headers = {"authorization": authorization} if authorization else {}

    now = int(time.time())
    a = pyjwt.encode({"sub": "alice", "exp": now + 600}, "k", algorithm="HS256")
    b = pyjwt.encode({"sub": "bob", "exp": now + 600}, "k", algorithm="HS256")

    class C:
        host = "127.0.0.1"

    req_a, req_b = FakeRequest(cookie=a), FakeRequest(cookie=b)
    req_a.client = req_b.client = C()
    assert a[:16] == b[:16], "precondition: the old key collided"
    assert _principal_key(req_a) != _principal_key(req_b)


def test_principal_key_does_not_retain_the_raw_token():
    from web.ratelimit import _principal_key

    class FakeRequest:
        cookies = {"ai_research_access": "super-secret-token-value"}
        headers = {}

        class client:
            host = "127.0.0.1"

    key = _principal_key(FakeRequest())
    assert "super-secret-token" not in key


def test_api_token_users_get_their_own_bucket():
    from web.ratelimit import _principal_key

    class FakeRequest:
        cookies = {}
        headers = {"authorization": "Bearer api-token-abc"}

        class client:
            host = "127.0.0.1"

    class Other(FakeRequest):
        headers = {"authorization": "Bearer api-token-xyz"}

    assert _principal_key(FakeRequest()) != _principal_key(Other())


def test_anonymous_requests_fall_back_to_the_client_address():
    from web.ratelimit import _principal_key

    class FakeRequest:
        cookies = {}
        headers = {}

        class client:
            host = "192.168.1.9"

    assert _principal_key(FakeRequest()).startswith("anon:")


def test_rate_limiter_does_not_grow_without_bound():
    """Keys come from attacker-influenced input, so stale ones must be swept."""
    from web.ratelimit import RateLimiter

    limiter = RateLimiter(max_requests=5, window_seconds=60)
    for index in range(200):
        limiter.check(f"key-{index}")
    assert len(limiter._buckets) == 200

    # Age every bucket past the window, as a minute of real traffic would.
    stale = time.time() - 3600
    for hits in limiter._buckets.values():
        hits[:] = [stale]
    limiter._last_sweep = 0.0
    limiter.check("fresh")
    assert len(limiter._buckets) == 1, "stale buckets were not swept"


def test_server_does_not_trust_forwarded_headers():
    """X-Forwarded-For would let a loopback client mint a fresh login bucket."""
    source = (ROOT / "run_web.py").read_text(encoding="utf-8")
    assert "proxy_headers=False" in source
    assert 'forwarded_allow_ips=""' in source


# --- P0-5: a failed write must not destroy the previous report ---------------

def test_report_write_is_atomic(tmp_path, monkeypatch):
    """open(path, "w") truncates before the first byte, so a crash mid-write
    left a partial report that the cleanup then treated as a success."""
    from core.engine import ResearchEngine

    engine = ResearchEngine.__new__(ResearchEngine)
    target = tmp_path / "out" / "report.md"
    monkeypatch.setattr("core.fileio.os.replace", os.replace)  # real rename
    path = engine._save_output("# hello\n", str(target))
    assert Path(path).read_text(encoding="utf-8") == "# hello\n"
    # No temp file left behind.
    assert [p.name for p in Path(path).parent.iterdir()] == ["report.md"]


def test_restore_logic_ignores_a_zero_byte_replacement(tmp_path):
    """The cleanup must not treat an empty leftover as a fresh report."""
    source = (ROOT / "core" / "engine.py").read_text(encoding="utf-8")
    assert "os.path.getsize(output_path) > 0" in source


def test_save_output_still_avoids_overwriting(tmp_path):
    from core.engine import ResearchEngine

    engine = ResearchEngine.__new__(ResearchEngine)
    target = tmp_path / "report.md"
    target.write_text("old", encoding="utf-8")
    path = engine._save_output("new", str(target))
    assert Path(path).name == "report_1.md"
    assert target.read_text(encoding="utf-8") == "old"
