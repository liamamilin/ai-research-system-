"""Webhook / IM / email notifications for job and round events.

Channels (all optional, any combination):

* ``webhook_url``  - generic JSON POST (Slack/Discord compatible ``text``/``content``)
* ``wecom``        - 企业微信群机器人 (markdown message)
* ``feishu``       - 飞书群机器人 (text message)
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

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
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


def _channels(cfg: dict) -> list[str]:
    """Which channels are configured and enabled."""
    available = []
    if cfg.get("webhook_url"):
        available.append("webhook")
    if (cfg.get("wecom") or {}).get("enabled") and (cfg.get("wecom") or {}).get("webhook_url"):
        available.append("wecom")
    if (cfg.get("feishu") or {}).get("enabled") and (cfg.get("feishu") or {}).get("webhook_url"):
        available.append("feishu")
    email_cfg = cfg.get("email") or {}
    if email_cfg.get("enabled") and email_cfg.get("smtp_host") and email_cfg.get("to_addrs"):
        available.append("email")
    return available


def is_enabled(event: str, sys_config: Optional[dict] = None) -> bool:
    cfg = _config(sys_config)
    if not cfg.get("enabled", False):
        return False
    if event not in (cfg.get("notify_on") or _DEFAULT_EVENTS):
        return False
    return bool(_channels(cfg))


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _post_json(url: str, payload: dict, timeout: int) -> bool:
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
        resp.read(64)
    return True


def _send_webhook(cfg: dict, text: str, payload: dict, timeout: int) -> bool:
    return _post_json(cfg["webhook_url"], payload, timeout)


def _send_wecom(cfg: dict, text: str, timeout: int) -> bool:
    body = {
        "msgtype": "markdown",
        "markdown": {"content": text[:_MAX_TEXT]},
    }
    return _post_json((cfg.get("wecom") or {})["webhook_url"], body, timeout)


def _send_feishu(cfg: dict, text: str, timeout: int) -> bool:
    body = {
        "msg_type": "text",
        "content": {"text": text[:_MAX_TEXT]},
    }
    return _post_json((cfg.get("feishu") or {})["webhook_url"], body, timeout)


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
            if channel == "webhook":
                ok = _send_webhook(cfg, text, payload, timeout)
            elif channel == "wecom":
                ok = _send_wecom(cfg, text, timeout)
            elif channel == "feishu":
                ok = _send_feishu(cfg, text, timeout)
            elif channel == "email":
                ok = _send_email(cfg, title, text)
            else:
                continue
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
