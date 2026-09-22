"""Webhook / IM / email notifications for job and round events.

Channels (all optional, any combination):

* ``webhook_url``  - generic JSON POST (Slack/Discord compatible ``text``/``content``)
* ``wecom``        - 企业微信群机器人 (markdown message)
* ``feishu``       - 飞书群机器人 (text message, optional signature)
* ``pushplus``     - 个人微信直推 via pushplus.plus
* ``serverchan``   - 个人微信直推 via sct.ftqq.com
* ``email``        - SMTP (SSL or STARTTLS)

Configured via ``system.yaml``::

    notifications:
      enabled: true
      notify_on: ["failed", "cancelled", "round_finished"]
      webhook_url: ""
      wecom:
        enabled: true
        webhook_url: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."
      feishu:
        enabled: true
        webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/..."
      email:
        enabled: true
        smtp_host: "smtp.example.com"
        smtp_port: 465
        smtp_user: "bot@example.com"
        smtp_password_env: "SMTP_PASSWORD"
        from_addr: "bot@example.com"
        to_addrs: ["me@example.com"]
        use_ssl: true
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import smtplib
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_EVENTS = ["failed", "cancelled", "round_finished"]
_MAX_TEXT = 3800  # keep IM payloads under common 4KB webhook limits


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _config(sys_config: Optional[dict]) -> dict:
    if sys_config is None:
        try:
            from .config import load_system_config

            sys_config = load_system_config("config")
        except Exception:  # noqa: BLE001 - notifications are best-effort
            sys_config = {}
    return sys_config.get("notifications", {}) or {}


def _channel_url(cfg: dict, channel: str) -> str:
    """Resolve a channel webhook URL, preferring its ``webhook_url_env``."""
    sub = cfg if channel == "webhook" else (cfg.get(channel) or {})
    env_name = sub.get("webhook_url_env")
    if env_name:
        return os.environ.get(env_name, "") or ""
    return sub.get("webhook_url") or ""


def _credential(sub: dict, env_key: str, inline_key: str) -> str:
    """Resolve a channel credential, preferring its ``*_env`` variable."""
    env_name = sub.get(env_key)
    if env_name:
        return os.environ.get(env_name, "") or ""
    return sub.get(inline_key) or ""


def _push_token(cfg: dict, channel: str) -> str:
    sub = cfg.get(channel) or {}
    if channel == "pushplus":
        return _credential(sub, "token_env", "token")
    return _credential(sub, "sendkey_env", "sendkey")


def _channels(cfg: dict) -> list[str]:
    """Which channels are configured and enabled."""
    available = []
    if _channel_url(cfg, "webhook"):
        available.append("webhook")
    if (cfg.get("wecom") or {}).get("enabled") and _channel_url(cfg, "wecom"):
        available.append("wecom")
    if (cfg.get("feishu") or {}).get("enabled") and _channel_url(cfg, "feishu"):
        available.append("feishu")
    for channel in ("pushplus", "serverchan"):
        if (cfg.get(channel) or {}).get("enabled") and _push_token(cfg, channel):
            available.append(channel)
    email_cfg = cfg.get("email") or {}
    if email_cfg.get("enabled") and email_cfg.get("smtp_host") and email_cfg.get("to_addrs"):
        available.append("email")
    return available


def is_enabled(event: str, sys_config: Optional[dict] = None) -> bool:
    cfg = _config(sys_config)
    if not cfg.get("enabled", False):
        return False
    if event != "test" and event not in (cfg.get("notify_on") or _DEFAULT_EVENTS):
        return False
    return bool(_channels(cfg))


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _check_bot_response(body: bytes, success_codes: tuple = (0,)) -> None:
    """Raise when a push service reports a failure code in its JSON body.

    WeCom, Feishu and PushPlus answer HTTP 200 even for rejected messages;
    the real status lives in ``errcode`` / ``code``.
    """
    if not body:
        return
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except (ValueError, UnicodeDecodeError):
        return
    if not isinstance(data, dict):
        return
    code = data.get("errcode", data.get("code"))
    if code is None:
        return
    try:
        code = int(code)
    except (TypeError, ValueError):
        return
    if code not in success_codes:
        msg = data.get("errmsg") or data.get("msg") or data.get("message") or ""
        raise RuntimeError(f"webhook rejected the message: code={code} {msg}")


def _post_json(url: str, payload: dict, timeout: int,
               success_codes: tuple = (0,)) -> bool:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "BaseCodingCLi-Notify/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = resp.read(4096)
    _check_bot_response(body, success_codes)
    return True


def _post_form(url: str, fields: dict, timeout: int,
               success_codes: tuple = (0,)) -> bool:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "BaseCodingCLi-Notify/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        body = resp.read(4096)
    _check_bot_response(body, success_codes)
    return True


def _send_webhook(cfg: dict, text: str, payload: dict, timeout: int) -> bool:
    return _post_json(_channel_url(cfg, "webhook"), payload, timeout)


def _send_wecom(cfg: dict, text: str, timeout: int) -> bool:
    body = {
        "msgtype": "markdown",
        "markdown": {"content": text[:_MAX_TEXT]},
    }
    return _post_json(_channel_url(cfg, "wecom"), body, timeout)


def _send_feishu(cfg: dict, text: str, timeout: int) -> bool:
    sub = cfg.get("feishu") or {}
    body: dict = {
        "msg_type": "text",
        "content": {"text": text[:_MAX_TEXT]},
    }
    secret = sub.get("secret") or os.environ.get(sub.get("secret_env", ""), "")
    if secret:
        timestamp = str(int(time.time()))
        digest = hmac.new(
            f"{timestamp}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        body["timestamp"] = timestamp
        body["sign"] = base64.b64encode(digest).decode("utf-8")
    return _post_json(_channel_url(cfg, "feishu"), body, timeout)


def _send_pushplus(cfg: dict, title: str, text: str, timeout: int) -> bool:
    sub = cfg.get("pushplus") or {}
    body = {
        "token": _push_token(cfg, "pushplus"),
        "title": title,
        "content": text[:_MAX_TEXT],
        "template": "markdown",
    }
    if sub.get("topic"):
        body["topic"] = sub["topic"]
    return _post_json("https://www.pushplus.plus/send", body, timeout,
                      success_codes=(200,))


def _send_serverchan(cfg: dict, title: str, text: str, timeout: int) -> bool:
    sendkey = _push_token(cfg, "serverchan")
    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    return _post_form(url, {"title": title, "desp": text[:_MAX_TEXT]}, timeout)


def _send_email(cfg: dict, title: str, text: str) -> bool:
    email_cfg = cfg.get("email") or {}
    password_env = email_cfg.get("smtp_password_env", "SMTP_PASSWORD")
    password = os.environ.get(password_env, "")
    host = email_cfg["smtp_host"]
    port = int(email_cfg.get("smtp_port", 465))
    user = email_cfg.get("smtp_user", "")
    from_addr = email_cfg.get("from_addr") or user
    to_addrs = list(email_cfg.get("to_addrs") or [])
    use_ssl = bool(email_cfg.get("use_ssl", True))

    message = MIMEText(text, "plain", "utf-8")
    message["Subject"] = f"[AI Research] {title}"
    message["From"] = from_addr
    message["To"] = ", ".join(to_addrs)

    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=15)
    else:
        server = smtplib.SMTP(host, port, timeout=15)
        server.starttls()
    try:
        if user:
            server.login(user, password)
        server.sendmail(from_addr, to_addrs, message.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass
    return True


def _deliver(channel: str, cfg: dict, timeout: int, title: str,
             text: str, payload: dict) -> bool:
    if channel == "webhook":
        return _send_webhook(cfg, text, payload, timeout)
    if channel == "wecom":
        return _send_wecom(cfg, text, timeout)
    if channel == "feishu":
        return _send_feishu(cfg, text, timeout)
    if channel == "pushplus":
        return _send_pushplus(cfg, title, text, timeout)
    if channel == "serverchan":
        return _send_serverchan(cfg, title, text, timeout)
    if channel == "email":
        return _send_email(cfg, title, text)
    raise ValueError(f"unknown channel: {channel}")


def send(
    event: str,
    title: str,
    message: str,
    fields: Optional[dict] = None,
    sys_config: Optional[dict] = None,
) -> bool:
    """Send a notification to every configured channel.

    Returns True when at least one channel delivered; never raises.
    """
    cfg = _config(sys_config)
    if not is_enabled(event, sys_config):
        return False

    timeout = int(cfg.get("timeout", 10))
    text = f"[{title}] {message}"
    payload: dict = {
        "event": event,
        "title": title,
        "message": message,
        "text": text,
        "content": text,
    }
    if fields:
        payload.update(fields)

    delivered = False
    for channel in _channels(cfg):
        try:
            ok = _deliver(channel, cfg, timeout, title, text, payload)
            if ok:
                delivered = True
                logger.info("Notification sent via %s (%s): %s",
                            channel, event, title)
        except Exception as exc:  # noqa: BLE001 - one bad channel must not block others
            logger.warning("Notification channel '%s' failed (%s): %s",
                           channel, event, str(exc)[:200])

    if not delivered:
        logger.warning("Notification not delivered (%s): %s", event, title)
    return delivered


def send_test(sys_config: Optional[dict] = None) -> dict:
    """Send a test message and report per-channel delivery results."""
    cfg = _config(sys_config)
    if not cfg.get("enabled", False):
        return {"enabled": False, "channels": [], "results": {}}

    channels = _channels(cfg)
    timeout = int(cfg.get("timeout", 10))
    title = "通知测试"
    text = f"[{title}] 这是一条测试消息 · {datetime.now():%Y-%m-%d %H:%M:%S}"
    payload = {
        "event": "test",
        "title": title,
        "message": text,
        "text": text,
        "content": text,
    }

    results: dict[str, bool] = {}
    for channel in channels:
        try:
            results[channel] = bool(_deliver(channel, cfg, timeout, title, text, payload))
        except Exception as exc:  # noqa: BLE001 - reported per channel
            logger.warning("Test notification channel '%s' failed: %s",
                           channel, str(exc)[:200])
            results[channel] = False
    return {"enabled": True, "channels": channels, "results": results}
