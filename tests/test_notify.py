"""Tests for webhook notifications."""

from __future__ import annotations

import json

from core import notify


CFG = {
    "notifications": {
        "enabled": True,
        "webhook_url": "http://hook.test/notify",
        "notify_on": ["failed", "round_finished"],
        "timeout": 3,
    }
}


def test_disabled_when_not_configured():
    assert notify.is_enabled("failed", {}) is False
    assert notify.send("failed", "t", "m", sys_config={}) is False


def test_event_filter():
    assert notify.is_enabled("failed", CFG) is True
    assert notify.is_enabled("success", CFG) is False


def test_payload_shape(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self, _n=None):
            return b"ok"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    ok = notify.send("round_finished", "轮次 2026-01-02", "状态 success",
                     fields={"failed_stages": []}, sys_config=CFG)
    assert ok is True
    assert captured["url"] == "http://hook.test/notify"
    body = captured["body"]
    # Slack + Discord compatible keys
    assert body["text"] == body["content"]
    assert body["event"] == "round_finished"
    assert body["failed_stages"] == []


def test_failure_never_raises(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    assert notify.send("failed", "t", "m", sys_config=CFG) is False


# ---------------------------------------------------------------------------
# IM channels (企业微信 / 飞书) and email
# ---------------------------------------------------------------------------


def _cfg(**overrides):
    base = {
        "notifications": {
            "enabled": True,
            "notify_on": ["failed", "round_finished"],
            "timeout": 3,
            **overrides,
        }
    }
    return base


class _FakeResp:
    def read(self, _n=None):
        return b"ok"

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _capture_urlopen(calls):
    def fake(request, timeout=None):
        calls.append((request.full_url, json.loads(request.data.decode("utf-8"))))
        return _FakeResp()

    return fake


def test_wecom_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    ok = notify.send("failed", "Job 失败", "boom", sys_config=_cfg(
        wecom={"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/hook?key=x"}
    ))
    assert ok is True
    url, body = calls[0]
    assert "qyapi.weixin.qq.com" in url
    assert body["msgtype"] == "markdown"
    assert "boom" in body["markdown"]["content"]


def test_feishu_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    ok = notify.send("round_finished", "轮次", "status success", sys_config=_cfg(
        feishu={"enabled": True, "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/x"}
    ))
    assert ok is True
    _, body = calls[0]
    assert body["msg_type"] == "text"
    assert "status success" in body["content"]["text"]


def test_email_channel(monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host
            captured["port"] = port

        def login(self, user, password):
            captured["login"] = (user, password)

        def sendmail(self, from_addr, to_addrs, raw):
            captured["mail"] = (from_addr, to_addrs, raw)

        def quit(self):
            captured["quit"] = True

    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("SMTP_PASSWORD", "s3cret")

    ok = notify.send("failed", "Job 失败: x", "error text", sys_config=_cfg(
        email={
            "enabled": True,
            "smtp_host": "smtp.test",
            "smtp_port": 465,
            "smtp_user": "bot@test",
            "smtp_password_env": "SMTP_PASSWORD",
            "from_addr": "bot@test",
            "to_addrs": ["me@test"],
            "use_ssl": True,
        }
    ))
    assert ok is True
    assert captured["host"] == "smtp.test" and captured["port"] == 465
    assert captured["login"] == ("bot@test", "s3cret")
    from_addr, to_addrs, raw = captured["mail"]
    assert from_addr == "bot@test" and to_addrs == ["me@test"]
    # Subject is RFC2047-encoded (contains CJK); decode before asserting
    from email import message_from_string
    from email.header import decode_header
    parsed = message_from_string(raw)
    subject = "".join(
        part.decode(enc or "utf-8") if isinstance(part, bytes) else part
        for part, enc in decode_header(parsed["Subject"])
    )
    assert "[AI Research]" in subject
    assert captured["quit"] is True


def test_multi_channel_one_failure_does_not_block(monkeypatch):
    calls = []

    def fake(request, timeout=None):
        calls.append(request.full_url)
        if "qyapi" in request.full_url:
            raise OSError("wecom down")
        return _FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    ok = notify.send("failed", "t", "m", sys_config=_cfg(
        webhook_url="http://hook.test/ok",
        wecom={"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/hook"},
    ))
    assert ok is True  # webhook delivered despite wecom failure
    assert len(calls) == 2


def test_no_channel_configured_is_disabled():
    cfg = _cfg()  # enabled but no channel configured
    assert notify.is_enabled("failed", cfg) is False
