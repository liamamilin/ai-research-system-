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


# ---------------------------------------------------------------------------
# Env-var webhook URLs and test command
# ---------------------------------------------------------------------------


def test_webhook_url_env_resolution(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    monkeypatch.setenv("WECOM_HOOK_TEST", "https://qyapi.weixin.qq.com/hook?key=env")

    cfg = _cfg(wecom={"enabled": True, "webhook_url_env": "WECOM_HOOK_TEST"})
    assert notify.is_enabled("failed", cfg) is True
    assert notify.send("failed", "t", "m", sys_config=cfg) is True
    assert calls[0][0].endswith("key=env")


def test_webhook_url_env_missing_is_disabled(monkeypatch):
    monkeypatch.delenv("WECOM_HOOK_MISSING", raising=False)
    cfg = _cfg(wecom={"enabled": True, "webhook_url_env": "WECOM_HOOK_MISSING"})
    assert notify.is_enabled("failed", cfg) is False


def test_env_url_takes_precedence_over_inline(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    monkeypatch.setenv("WECOM_HOOK_TEST", "https://qyapi.weixin.qq.com/hook?key=env")
    cfg = _cfg(wecom={
        "enabled": True,
        "webhook_url": "https://qyapi.weixin.qq.com/hook?key=inline",
        "webhook_url_env": "WECOM_HOOK_TEST",
    })
    notify.send("failed", "t", "m", sys_config=cfg)
    assert calls[0][0].endswith("key=env")


def test_test_event_bypasses_notify_on_filter():
    cfg = _cfg(webhook_url="http://hook.test/notify")
    cfg["notifications"]["notify_on"] = ["failed"]
    assert notify.is_enabled("round_finished", cfg) is False
    assert notify.is_enabled("test", cfg) is True


def test_send_test_reports_per_channel(monkeypatch):
    def fake(request, timeout=None):
        if "qyapi" in request.full_url:
            raise OSError("wecom down")
        return _FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    report = notify.send_test(sys_config=_cfg(
        webhook_url="http://hook.test/ok",
        wecom={"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/hook"},
    ))
    assert report["enabled"] is True
    assert set(report["channels"]) == {"webhook", "wecom"}
    assert report["results"] == {"webhook": True, "wecom": False}


def test_send_test_disabled_configuration():
    report = notify.send_test(sys_config={"notifications": {"enabled": False}})
    assert report == {"enabled": False, "channels": [], "results": {}}


# ---------------------------------------------------------------------------
# Bot error codes inside HTTP 200 responses
# ---------------------------------------------------------------------------


def _respond_with(body: bytes):
    class _Resp(_FakeResp):
        def read(self, _n=None):
            return body

    def fake(request, timeout=None):
        return _Resp()

    return fake


def test_wecom_error_code_marks_failure(monkeypatch):
    monkeypatch.setattr(
        notify.urllib.request, "urlopen",
        _respond_with(b'{"errcode":93000,"errmsg":"invalid webhook url"}'),
    )
    cfg = _cfg(wecom={"enabled": True, "webhook_url": "https://qyapi.weixin.qq.com/hook"})
    assert notify.send("failed", "t", "m", sys_config=cfg) is False
    report = notify.send_test(sys_config=cfg)
    assert report["results"] == {"wecom": False}


def test_feishu_signature_payload(monkeypatch):
    import base64
    import hashlib
    import hmac

    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    ok = notify.send("failed", "t", "m", sys_config=_cfg(
        feishu={"enabled": True, "webhook_url": "https://open.feishu.cn/hook/x",
                "secret": "s3cret"},
    ))
    assert ok is True
    _, body = calls[0]
    assert body["timestamp"]
    expected = base64.b64encode(hmac.new(
        f"{body['timestamp']}\ns3cret".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()).decode("utf-8")
    assert body["sign"] == expected


def test_feishu_without_secret_has_no_signature(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    notify.send("failed", "t", "m", sys_config=_cfg(
        feishu={"enabled": True, "webhook_url": "https://open.feishu.cn/hook/x"},
    ))
    _, body = calls[0]
    assert "sign" not in body and "timestamp" not in body


def test_feishu_secret_env(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    monkeypatch.setenv("FEISHU_BOT_SECRET_TEST", "env-secret")
    notify.send("failed", "t", "m", sys_config=_cfg(
        feishu={"enabled": True, "webhook_url": "https://open.feishu.cn/hook/x",
                "secret_env": "FEISHU_BOT_SECRET_TEST"},
    ))
    _, body = calls[0]
    assert "sign" in body


def test_feishu_error_code_marks_failure(monkeypatch):
    monkeypatch.setattr(
        notify.urllib.request, "urlopen",
        _respond_with(b'{"code":19001,"msg":"invalid access token"}'),
    )
    cfg = _cfg(feishu={"enabled": True, "webhook_url": "https://open.feishu.cn/hook/x"})
    assert notify.send("failed", "t", "m", sys_config=cfg) is False


def test_pushplus_payload_and_env_token(monkeypatch):
    calls = []
    monkeypatch.setattr(notify.urllib.request, "urlopen", _capture_urlopen(calls))
    monkeypatch.setenv("PUSHPLUS_TOKEN_TEST", "pp-token")
    ok = notify.send("failed", "Job 失败", "boom", sys_config=_cfg(
        pushplus={"enabled": True, "token_env": "PUSHPLUS_TOKEN_TEST", "topic": "ops"},
    ))
    assert ok is True
    url, body = calls[0]
    assert url == "https://www.pushplus.plus/send"
    assert body["token"] == "pp-token"
    assert body["title"] == "Job 失败"
    assert "boom" in body["content"]
    assert body["topic"] == "ops"


def test_pushplus_missing_token_is_disabled(monkeypatch):
    monkeypatch.delenv("PUSHPLUS_MISSING", raising=False)
    cfg = _cfg(pushplus={"enabled": True, "token_env": "PUSHPLUS_MISSING"})
    assert notify.is_enabled("failed", cfg) is False


def test_pushplus_error_code_marks_failure(monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        _respond_with(b'{"code":401,"msg":"token invalid"}'))
    cfg = _cfg(pushplus={"enabled": True, "token": "bad"})
    assert notify.send("failed", "t", "m", sys_config=cfg) is False


def test_pushplus_success_code_200(monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        _respond_with(b'{"code":200,"msg":"ok"}'))
    cfg = _cfg(pushplus={"enabled": True, "token": "good"})
    assert notify.send("failed", "t", "m", sys_config=cfg) is True


def test_serverchan_posts_form_with_sendkey(monkeypatch):
    captured = {}

    class _Resp(_FakeResp):
        def read(self, _n=None):
            return b'{"code":0,"message":"ok"}'

    def fake(request, timeout=None):
        captured["url"] = request.full_url
        captured["ctype"] = request.get_header("Content-type")
        captured["body"] = request.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake)
    monkeypatch.setenv("SERVERCHAN_KEY_TEST", "SCT123")
    ok = notify.send("round_finished", "轮次", "status success", sys_config=_cfg(
        serverchan={"enabled": True, "sendkey_env": "SERVERCHAN_KEY_TEST"},
    ))
    assert ok is True
    assert captured["url"] == "https://sctapi.ftqq.com/SCT123.send"
    assert captured["ctype"] == "application/x-www-form-urlencoded"
    assert "title=%E8%BD%AE%E6%AC%A1" in captured["body"]
    assert "status+success" in captured["body"]


def test_serverchan_error_code_marks_failure(monkeypatch):
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        _respond_with(b'{"code":40001,"message":"bad key"}'))
    cfg = _cfg(serverchan={"enabled": True, "sendkey": "bad"})
    assert notify.send("failed", "t", "m", sys_config=cfg) is False


def test_success_codes_and_plain_bodies_are_accepted(monkeypatch):
    cfg = _cfg(webhook_url="http://hook.test/notify")
    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        _respond_with(b'{"errcode":0,"errmsg":"ok"}'))
    assert notify.send("failed", "t", "m", sys_config=cfg) is True

    monkeypatch.setattr(notify.urllib.request, "urlopen",
                        _respond_with(b"plain text body"))
    assert notify.send("failed", "t", "m", sys_config=cfg) is True

    monkeypatch.setattr(notify.urllib.request, "urlopen", _respond_with(b""))
    assert notify.send("failed", "t", "m", sys_config=cfg) is True
