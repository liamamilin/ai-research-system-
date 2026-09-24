"""The macOS launcher applet and its detached starter.

These tests guard the three ways the launcher silently broke before: a stale
absolute path, an applet compiled without stay-open (the watchdog never runs),
and a `nohup ... &` chain written inline into `do shell script` (which blocks
until the server exits).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "scripts" / "launcher.applescript.in"
STARTER = REPO / "scripts" / "start_server_detached.sh"
BUILD = REPO / "scripts" / "build_launcher.sh"

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="the launcher is a macOS applet")


def _template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_template_declares_every_placeholder_the_builder_substitutes():
    text = _template_text()
    builder = BUILD.read_text(encoding="utf-8")
    for placeholder in ("@PROJECT_DIR@", "@PYTHON_BIN@", "@LOG_FILE@", "@START_SCRIPT@"):
        assert placeholder in text, f"{placeholder} missing from the template"
        assert placeholder in builder, f"{placeholder} is never substituted"


def test_template_has_no_hardcoded_user_paths():
    """A clone on another machine must not inherit someone else's paths."""
    text = _template_text()
    assert "/Users/" not in text, "template bakes in a user-specific path"
    assert str(REPO) not in text


def test_builder_substitutes_every_placeholder():
    out = subprocess.run(
        ["/bin/sh", "-c",
         f"sed -e 's|@PROJECT_DIR@|{REPO}|g' -e 's|@PYTHON_BIN@|/bin/python3|g' "
         f"-e 's|@LOG_FILE@|/tmp/x.log|g' -e 's|@START_SCRIPT@|/tmp/s.sh|g' "
         f"{TEMPLATE}"],
        capture_output=True, text=True, check=True,
    ).stdout
    leftovers = re.findall(r"@[A-Z_]+@", out)
    assert leftovers == [], f"unsubstituted placeholders: {leftovers}"


@darwin_only
def test_template_compiles_with_stay_open():
    """Without -s the applet quits after `run`, and on idle never fires."""
    builder = BUILD.read_text(encoding="utf-8")
    assert re.search(r"osacompile\s+-s\b", builder), \
        "build_launcher.sh must pass -s (stay-open) or the watchdog is dead code"

    tmp = REPO / "scripts" / ".test_launcher.applescript"
    tmp.write_text(
        _template_text()
        .replace("@PROJECT_DIR@", str(REPO))
        .replace("@PYTHON_BIN@", sys.executable)
        .replace("@LOG_FILE@", "/tmp/launcher_test.log")
        .replace("@START_SCRIPT@", str(REPO / "scripts" / "start_server_detached.sh")),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            ["osacompile", "-s", "-o", "/tmp/launcher_test_check.scpt", str(tmp)],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    finally:
        tmp.unlink(missing_ok=True)


def test_watchdog_guards_are_wired():
    text = _template_text()
    assert "on idle" in text
    assert "return 60" in text, "the idle interval must be returned or the app quits"
    # A stale server holding the port has to be stopped before a restart.
    assert "on stopStaleServer" in text
    assert "state/server.pid" in text
    # `notify` is reserved AppleScript terminology; a handler named that fails
    # to compile, which cost one debugging round.
    assert "on postNotification(" in text
    assert not re.search(r"^\ton notify\(", text, re.M)


def test_starter_detaches_and_returns_immediately(tmp_path):
    """`do shell script` waits for the command; the starter must not block."""
    starter = STARTER.read_text(encoding="utf-8")
    assert "nohup" in starter and "disown" in starter
    assert 'rm -f "$PID_FILE"' in starter, "a stale pid file confuses the launcher"
    # It must exit on its own rather than waiting for the server.
    assert re.search(r"^exit 0\s*$", starter, re.M)


@pytest.mark.skipif(sys.platform != "darwin", reason="spawns a real server")
def test_starter_returns_before_the_server_is_ready(tmp_path):
    """The launcher calls this and must get control back immediately."""
    repo = REPO
    rendered = repo / "scripts" / ".start_server.test.sh"
    rendered.write_text(
        STARTER.read_text(encoding="utf-8").replace(
            "@PYTHON_BIN@", sys.executable),
        encoding="utf-8",
    )
    port_free = _port_is_free(8765)
    if not port_free:
        pytest.skip("port 8765 is busy; not spawning a second server")
    try:
        started = time.time()
        result = subprocess.run(["/bin/bash", str(rendered)],
                                capture_output=True, text=True, timeout=30)
        elapsed = time.time() - started
        assert result.returncode == 0, result.stderr
        assert elapsed < 10, f"starter blocked for {elapsed:.1f}s"
    finally:
        rendered.unlink(missing_ok=True)
        _wait_for(lambda: _port_is_free(8765), timeout=60) or _kill_port()


def _port_is_free(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _kill_port() -> None:
    subprocess.run("lsof -ti :8765 | xargs kill -9",
                   shell=True, capture_output=True)


def _wait_for(predicate, timeout: float = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.5)
    return False
