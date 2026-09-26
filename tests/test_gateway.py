"""The permanent gateway in front of the console.

Two things are worth more than a passing suite here: that a request revives a
stopped app exactly once (no spawn storm), and that a log stream is forwarded
incrementally instead of being buffered into one lump at the end. Both are
exercised against a throwaway upstream on a loopback port, never the real app.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_gateway():
    """Import scripts/gateway.py by path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("arec_gateway", ROOT / "scripts" / "gateway.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


gateway = _load_gateway()


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class FakeApp:
    """A stand-in for the console: health, JSON, and a slow event stream."""

    def __init__(self):
        self.port = free_port()
        self.hits: list[str] = []
        self.stream_started = threading.Event()
        self.release = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def _send(self, status, body: bytes, ctype="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path.startswith("/api/health"):
                    self._send(200, b'{"status":"ok"}')
                elif self.path.startswith("/stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    outer.stream_started.set()
                    for i in range(3):
                        chunk = f"data: tick {i}\n\n".encode()
                        self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                        self.wfile.flush()
                        # Hold the stream open so the test can prove the first
                        # event reached the client before the last one.
                        outer.release.wait(timeout=5)
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self._send(200, json.dumps({"path": self.path,
                                               "host": self.headers.get("Host"),
                                               "xf": self.headers.get("X-Forwarded-For")}).encode())

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                self._send(200, json.dumps({"echo": json.loads(body or b"{}"),
                                            "csrf": self.headers.get("X-CSRF-Token")}).encode())

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def handle_error(self, request, client_address):
                # A client that walks away mid-stream resets the socket; that is
                # routine and only makes the test output unreadable.
                kind = sys.exc_info()[0]
                if kind and issubclass(kind, (ConnectionResetError, BrokenPipeError)):
                    return
                super().handle_error(request, client_address)

        self.server = Server(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def restart(self):
        """Bring the fake app back after a test stopped it."""
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port),
                                          self.server.RequestHandlerClass)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def app():
    fake = FakeApp()
    yield fake
    fake.stop()


@pytest.fixture()
def front(app, tmp_path):
    """A gateway handler wired to the fake app, served on its own port."""
    port = free_port()
    handler = type("Bound", (gateway.GatewayHandler,), {
        "supervisor": gateway.Supervisor(app_port=app.port, project=tmp_path,
                                         cooldown=0.0, health_timeout=0.5),
        "public_port": port, "lan_access": False, "lan_host": "127.0.0.1",
    })

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = Server(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}", app
    server.shutdown()
    server.server_close()


def get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


# --- proxying ----------------------------------------------------------------

def test_proxies_a_request_to_the_app(front):
    base, _app = front
    status, body = get(f"{base}/api/reports?page=2")
    assert status == 200
    payload = json.loads(body)
    assert payload["path"] == "/api/reports?page=2"
    # The app must see its real port, not the public one.
    assert payload["host"].endswith(str(_app.port))


def test_forwards_the_request_body_and_csrf_header(front):
    base, _app = front
    req = urllib.request.Request(f"{base}/api/jobs", method="POST",
                                 data=json.dumps({"name": "x"}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-CSRF-Token": "tok123"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = json.loads(resp.read())
    assert payload["echo"] == {"name": "x"}
    assert payload["csrf"] == "tok123"


def test_reports_its_own_status_without_proxying_the_request(front):
    base, app = front
    app.hits.clear()
    status, body = get(f"{base}/__gateway/status")
    assert status == 200
    payload = json.loads(body)
    assert payload["app"]["healthy"] is True
    assert payload["app"]["app_port"] == app.port
    assert payload["lan_access"] is False
    # The supervisor's own health probe is expected; the status URL must not be
    # forwarded upstream.
    assert not any("__gateway" in hit for hit in app.hits)


def test_lan_url_is_advertised_only_when_enabled(front):
    base, _app = front
    _status, body = get(f"{base}/__gateway/status")
    assert json.loads(body)["lan_url"] == ""


# --- streaming ---------------------------------------------------------------

def test_streams_events_incrementally_rather_than_buffering(front):
    base, app = front
    received: list[str] = []

    def read_stream():
        with urllib.request.urlopen(f"{base}/stream", timeout=15) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:"):
                    received.append(line)

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()
    # The first event must arrive while the upstream is still holding the rest
    # of the stream open. A buffering proxy would deliver nothing until the end.
    assert app.stream_started.wait(timeout=5)
    deadline = time.time() + 5
    while len(received) < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert received, "no event arrived before the stream finished"
    app.release.set()
    reader.join(timeout=10)
    assert len(received) == 3


# --- reviving a stopped app --------------------------------------------------

def test_a_document_request_serves_the_starting_page_when_the_app_is_down(app, front):
    base, _app = front
    app.server.shutdown()
    app.server.server_close()
    try:
        req = urllib.request.Request(f"{base}/", headers={"Accept": "text/html"})
        try:
            urllib.request.urlopen(req, timeout=10)
            raise AssertionError("expected a 503 starting page")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            body = exc.read().decode("utf-8")
        assert "正在启动" in body
        assert 'http-equiv="refresh"' in body
    finally:
        app.restart()


def test_an_api_request_gets_a_status_code_not_html(app, front):
    base, _app = front
    app.server.shutdown()
    app.server.server_close()
    try:
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/reports"), timeout=10)
            raise AssertionError("expected a 503")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            payload = json.loads(exc.read())
        assert payload["error"]["code"] == "app_starting"
    finally:
        app.restart()


# --- the supervisor ----------------------------------------------------------

def test_healthy_app_is_left_alone(tmp_path):
    fake = FakeApp()
    try:
        sup = gateway.Supervisor(app_port=fake.port, project=tmp_path, cooldown=0.0)
        assert sup.ensure() == (True, "running")
        assert sup.spawns == 0
    finally:
        fake.stop()


def test_concurrent_requests_spawn_the_app_only_once(tmp_path):
    """Several browser requests during a cold start must not fork N servers."""
    app = FakeApp()
    sup = gateway.Supervisor(app_port=app.port, project=tmp_path, cooldown=30.0)
    app.stop()

    results: list[tuple[bool, str]] = []
    lock = threading.Lock()

    def hit():
        outcome = sup.ensure()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 8
    assert all(healthy is False for healthy, _ in results)
    assert sup.spawns == 1, "a cold start must not fork a server per request"
    assert sup.last_error == ""


def test_respawn_cooldown_blocks_a_retry_storm(tmp_path):
    app = FakeApp()
    sup = gateway.Supervisor(app_port=app.port, project=tmp_path, cooldown=30.0)
    app.stop()
    sup.ensure()
    assert sup.spawns == 1
    for _ in range(5):
        healthy, note = sup.ensure()
        assert healthy is False and note == "starting"
    assert sup.spawns == 1


def test_a_booting_app_is_not_respawned_over(tmp_path):
    """The app binds its port ~12s before it answers; that gap must not fork a server.

    A cooldown alone is not enough: with cooldown=0 a request arriving during the
    boot window would start a second app into a port that is already taken.
    """
    # A socket that listens but never replies is exactly the boot window. The
    # backlog is generous because ensure() probes twice (health, then claimed),
    # and uvicorn's default backlog is far larger than one.
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    held.bind(("127.0.0.1", 0))
    held.listen(64)
    port = held.getsockname()[1]
    sup = gateway.Supervisor(app_port=port, project=tmp_path, cooldown=0.0)
    try:
        healthy, note = sup.ensure()
        assert healthy is False and note == "starting"
        assert sup.spawns == 0, "must not fork a second app over a booting one"
    finally:
        held.close()


def test_claimed_is_false_when_nothing_listens(tmp_path):
    sup = gateway.Supervisor(app_port=free_port(), project=tmp_path)
    assert sup.claimed() is False


def test_a_failing_spawn_is_reported_not_swallowed(tmp_path):
    sup = gateway.Supervisor(app_port=free_port(), project=tmp_path / "missing",
                             cooldown=0.0, python="/nonexistent/python")
    healthy, note = sup.ensure()
    assert healthy is False
    assert note.startswith("spawn_failed")


def test_health_probe_does_not_raise_when_nothing_listens(tmp_path):
    sup = gateway.Supervisor(app_port=free_port(), project=tmp_path, cooldown=1.0)
    assert sup.healthy() is False
    assert sup.status()["healthy"] is False


# --- LAN address detection ---------------------------------------------------

def test_loopback_and_link_local_are_not_advertised():
    from utils.net import is_usable_lan
    for bad in ("127.0.0.1", "169.254.10.1", "0.0.0.0", "not.an.ip", "", "1.2.3"):
        assert is_usable_lan(bad) is False, bad


def test_vpn_tunnel_address_is_not_advertised():
    """198.18.0.0/15 is what a TUN claims by default; a phone cannot reach it."""
    from utils.net import is_usable_lan
    for tunnel in ("198.18.0.1", "198.19.255.254"):
        assert is_usable_lan(tunnel) is False, tunnel
    # The private ranges a phone genuinely shares are fine.
    for real in ("192.168.2.105", "10.0.0.7", "172.16.4.4"):
        assert is_usable_lan(real) is True, real


def test_lan_address_prefers_hardware_over_a_tunnel(monkeypatch):
    from utils import net
    # This machine's shape: WiFi on en0 plus a VPN on utun8.
    monkeypatch.setattr(net, "interface_ipv4s", lambda: [
        ("lo0", "127.0.0.1"), ("en0", "192.168.2.105"), ("utun8", "198.18.0.1"),
    ])
    assert net.lan_address() == "192.168.2.105"


def test_lan_address_is_empty_when_only_a_tunnel_exists(monkeypatch):
    """Better to show nothing than a link that will not open."""
    from utils import net
    monkeypatch.setattr(net, "interface_ipv4s", lambda: [
        ("lo0", "127.0.0.1"), ("utun8", "198.18.0.1"),
    ])
    assert net.lan_address() == ""


def test_lan_address_falls_back_to_any_private_interface(monkeypatch):
    from utils import net
    monkeypatch.setattr(net, "interface_ipv4s", lambda: [
        ("lo0", "127.0.0.1"), ("wlp3s0", "10.1.2.3"),
    ])
    assert net.lan_address() == "10.1.2.3"


def test_lan_address_returns_an_address_or_empty():
    value = gateway.lan_address()
    assert isinstance(value, str)
    if value:
        from utils.net import is_usable_lan
        assert is_usable_lan(value), f"{value} is not a usable LAN address"


# --- settings plumbing -------------------------------------------------------

def test_app_moves_off_the_public_port_when_the_gateway_is_on():
    from web.settings import GatewayConfig, ServerConfig, WebSettings

    off = WebSettings(server=ServerConfig(port=8765))
    assert off.app_port() == 8765
    assert off.bind_host() == "127.0.0.1"

    on = WebSettings(server=ServerConfig(port=8765),
                     gateway=GatewayConfig(enabled=True, app_port=8766))
    assert on.app_port() == 8766, "the app must not fight the gateway for 8765"
    assert on.bind_host() == "127.0.0.1"


def test_only_the_gateway_binds_all_interfaces_for_lan_access():
    from web.settings import GatewayConfig, ServerConfig, WebSettings

    lan = WebSettings(server=ServerConfig(port=8765),
                      gateway=GatewayConfig(enabled=True, app_port=8766, lan_access=True))
    assert lan.bind_host() == "0.0.0.0"
    # LAN access without the gateway would expose the app itself.
    bare = WebSettings(server=ServerConfig(port=8765),
                       gateway=GatewayConfig(enabled=False, lan_access=True))
    assert bare.bind_host() == "127.0.0.1"
