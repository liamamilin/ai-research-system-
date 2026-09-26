"""Finding this machine's LAN address.

Shared by the gateway (which must not depend on the project's venv) and the
settings API, so the URL shown in the UI is the same one the listener uses.

The obvious trick -- open a UDP socket, "connect" it to a public address, and
read ``getsockname()`` -- is wrong here. It returns whatever the default route
would use, which on a machine with a VPN or a TUN interface is the *tunnel*
address (198.18.0.0/15 on macOS). Printing that as the phone URL produces a
link that simply never opens, so interfaces are enumerated and filtered instead.
"""

from __future__ import annotations

import subprocess

__all__ = ["interface_ipv4s", "is_usable_lan", "lan_address"]


def interface_ipv4s() -> list[tuple[str, str]]:
    """(interface, address) for every IPv4 on this host.

    Parsed from ``ifconfig`` / ``ip`` because the standard library cannot
    enumerate interfaces. Tries macOS first, then the Linux spelling.
    """
    found: list[tuple[str, str]] = []
    for command in (["ifconfig"], ["ip", "-4", "-o", "addr"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode:
            continue
        current = ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            # ifconfig names the interface on a column-0 line; `ip -o` puts the
            # name on the same line as the address.
            if command[0] == "ifconfig":
                if stripped and not line[0].isspace():
                    current = stripped.split(":")[0].split()[0]
            elif "inet " in stripped:
                current = stripped.split()[1].strip(":")
            if "inet " not in line:
                continue
            address = stripped.split("inet ")[1].split()[0].split("/")[0]
            if current:
                found.append((current, address))
        if found:
            break
    return found


def is_usable_lan(address: str) -> bool:
    """Whether an address belongs on a URL another device can open.

    Accepts only RFC 1918 private ranges, and rejects:
      * loopback (127/8),
      * link-local (169.254/16) -- a disconnected interface,
      * 198.18.0.0/15 -- the RFC 2544 benchmarking range that VPN/TUN drivers
        claim by default, which is a tunnel address rather than a real one.
    """
    parts = address.split(".")
    if len(parts) != 4 or not all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return False
    first, second = int(parts[0]), int(parts[1])
    if first == 127:
        return False
    if first == 169 and second == 254:
        return False
    if first == 198 and second in (18, 19):
        return False
    return (first == 10
            or (first == 172 and 16 <= second <= 31)
            or (first == 192 and second == 168))


def lan_address() -> str:
    """This machine's LAN IP, or an empty string when there isn't a usable one."""
    candidates = interface_ipv4s()
    # Prefer the usual hardware names. A utun/tap/tailscale name is usually not
    # the interface the phone shares, even when its address looks private.
    for wanted in ("en0", "en1", "en2", "en3", "wifi", "wlp"):
        for name, address in candidates:
            if name == wanted and is_usable_lan(address):
                return address
    for _name, address in candidates:
        if is_usable_lan(address):
            return address
    return ""
