"""Read and toggle the gateway settings that live in ``config/web.yaml``.

The gateway owns the public port, so a change to it cannot be applied by the
process handling the request -- the listener would have to rebind under itself.
Rather than half-restarting, the endpoint writes the file, says plainly that a
restart is required, and audits who asked.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StrictBool

from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services import yaml_io
from web import settings as web_settings
from web.settings import get_settings

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


def _error(code: int, kind: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=ApiError.make(kind, message))


class GatewayUpdate(BaseModel):
    # `enabled` is deliberately not editable here: it moves the app to another
    # port and needs the launchd agent reinstalled, which an HTTP call should
    # not pretend it can do.
    lan_access: StrictBool


def _web_yaml_path() -> str:
    """The file the running settings were actually read from.

    Not derived from paths.config_dir: AI_RESEARCH_WEB_CONFIG can point
    somewhere else, and writing to a different file than the one in use is how
    a setting silently does nothing.
    """
    settings = get_settings()
    if settings.source_path:
        return settings.source_path
    return os.path.join(settings.paths.config_dir, "web.yaml")


def _lan_address() -> str:
    """Shared with the gateway so both agree on the phone URL.

    One implementation on purpose: a second copy would eventually drift, and the
    failure mode is a link that silently does not open on a phone.
    """
    from utils.net import lan_address

    return lan_address()


def _describe() -> dict[str, Any]:
    settings = get_settings()
    gateway = settings.gateway
    address = _lan_address() if gateway.lan_access else ""
    return {
        "enabled": gateway.enabled,
        "lan_access": gateway.lan_access,
        "public_port": settings.server.port,
        "app_port": settings.app_port(),
        "bind_host": settings.bind_host(),
        "local_url": f"http://127.0.0.1:{settings.server.port}/",
        # Empty unless LAN access is on, so the UI never shows an address that
        # would not actually work.
        "lan_url": f"http://{address}:{settings.server.port}/" if gateway.lan_access else "",
        "lan_address": address,
        "secure_cookies": settings.auth.cookie_secure,
    }


@router.get("")
def read_gateway(user=Depends(require_admin)):
    return _describe()


@router.put("")
def update_gateway(payload: GatewayUpdate, request: Request, user=Depends(require_admin)):
    path = _web_yaml_path()
    try:
        data = yaml_io.parse_yaml(open(path, encoding="utf-8").read())
    except OSError as exc:
        raise _error(503, "config_unreadable", f"无法读取 web.yaml：{exc}") from exc
    if not isinstance(data, dict):
        raise _error(422, "invalid_config", "web.yaml 顶层不是映射，无法修改")

    section = data.get("gateway")
    if not isinstance(section, dict):
        section = {}
    changed = section.get("lan_access") != payload.lan_access
    if changed:
        section["lan_access"] = payload.lan_access
        data["gateway"] = section
        try:
            yaml_io.atomic_write(path, yaml_io.dump_yaml(data))
        except OSError as exc:
            raise _error(503, "config_unwritable", f"无法写入 web.yaml：{exc}") from exc

    ip = request.client.host if request.client else None
    audit.log("gateway_update", user=user["username"], target="gateway",
              details={"lan_access": payload.lan_access, "changed": changed}, ip=ip)
    if changed:
        # Drop the cached settings so the response describes what was just
        # written. Without this the UI checkbox would snap straight back, since
        # get_settings() still holds the pre-write values.
        web_settings.reset_settings()
    return {
        **_describe(),
        "changed": changed,
        # The listener has to rebind, and it cannot do that from inside the
        # request it is serving. Say so rather than pretend it took effect.
        "restart_required": changed,
        "restart_hint": ("bash scripts/install_launchd.sh --uninstall && "
                         "bash scripts/install_launchd.sh"),
    }
