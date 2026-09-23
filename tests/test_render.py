"""Tests for HTML export and report email."""

from __future__ import annotations

import pytest

from core.render import render_markdown, render_report_html


def test_render_markdown_tables_and_code():
    html = render_markdown(
        "# 标题\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```python\nprint(1)\n```\n")
    assert "<h1" in html and "标题" in html
    assert "<table>" in html and "<th>" in html
    assert "<code" in html and "print(1)" in html


def test_render_report_html_standalone():
    doc = render_report_html("My Report", "正文")
    assert doc.startswith("<!DOCTYPE html>")
    assert "<title>My Report</title>" in doc
    assert "正文" in doc


def test_render_escapes_title():
    doc = render_report_html("<script>alert(1)</script>", "x")
    assert "<script>alert(1)</script>" not in doc.split("<body>")[0]


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


@pytest.fixture()
def report(web_env):
    tmp_path, _ = web_env
    path = tmp_path / "output" / "demo" / "r.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 报告\n\n| A |\n|---|\n| 1 |", encoding="utf-8")
    return "demo/r.md"


def test_html_export_endpoint(client, report):
    _login(client, "viewer", "viewer-pass-123")
    r = client.get("/api/reports/html", params={"path": report})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<table>" in r.text

    d = client.get("/api/reports/html", params={"path": report, "download": "true"})
    assert "attachment" in d.headers.get("content-disposition", "")


def test_html_export_errors(client, report):
    assert client.get("/api/reports/html", params={"path": report}).status_code == 401
    _login(client)
    assert client.get("/api/reports/html", params={"path": "nope.md"}).status_code == 404


def _write_email_config(web_env):
    tmp_path, _ = web_env
    (tmp_path / "config" / "system.yaml").write_text(
        "notifications:\n"
        "  enabled: false\n"
        "  email:\n"
        "    enabled: true\n"
        "    smtp_host: smtp.test\n"
        "    smtp_port: 465\n"
        "    smtp_user: bot@test\n"
        "    from_addr: bot@test\n"
        "    to_addrs: [\"me@test\"]\n"
        "    use_ssl: true\n",
        encoding="utf-8",
    )


def test_email_report_not_configured(client, report):
    _login(client)
    r = client.post("/api/reports/email", json={"path": report}, headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "email_not_configured"


def test_email_report_sends(client, report, web_env, monkeypatch):
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            captured["host"] = host

        def login(self, user, password):
            captured["login"] = user

        def sendmail(self, from_addr, to_addrs, raw):
            captured["to"] = to_addrs
            captured["raw"] = raw

        def quit(self):
            pass

    import core.notify as notify
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    _write_email_config(web_env)

    _login(client)
    r = client.post("/api/reports/email",
                    json={"path": report, "to": ["extra@test"]},
                    headers=_csrf(client))
    assert r.status_code == 200
    assert captured["host"] == "smtp.test"
    assert captured["to"] == ["extra@test"]

    import email
    msg = email.message_from_string(captured["raw"])
    body = msg.get_payload(decode=True).decode("utf-8")
    assert "报告" in body and "| A |" in body


def test_email_report_requires_editor(client, report, web_env):
    _write_email_config(web_env)
    _login(client, "viewer", "viewer-pass-123")
    r = client.post("/api/reports/email", json={"path": report}, headers=_csrf(client))
    assert r.status_code == 403
