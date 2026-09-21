#!/usr/bin/env python3
"""AI Research Console - Web UI entry point.

Usage:
  python run_web.py serve                       Start the server
  python run_web.py stop                        Stop the running server
  python run_web.py restart                     Restart the server
  python run_web.py create-admin <user> <pwd>   Create the first admin
  python run_web.py create-user <user> <pwd> <role>  Create a user
  python run_web.py reindex                     Force re-scan output/
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

PID_FILE = "state/server.pid"


def cmd_serve(args):
    import uvicorn
    from web.settings import get_settings

    settings = get_settings()
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    # Write PID file
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))

    print(f"Starting AI Research Console on http://{host}:{port}")
    print(f"(API docs: http://{host}:{port}/docs)")

    try:
        uvicorn.run(
            "web.server:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=args.log_level,
        )
    finally:
        try:
            if os.path.isfile(PID_FILE):
                os.remove(PID_FILE)
        except OSError:
            pass


def cmd_stop(args):
    """Stop the running server by reading PID file."""
    if not os.path.isfile(PID_FILE):
        print("Server is not running (no PID file found).", file=sys.stderr)
        sys.exit(1)

    with open(PID_FILE) as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}...")

        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                print(f"Server (PID {pid}) stopped.")
                return
        print(f"Server (PID {pid}) did not stop, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"PID {pid} not found (server already stopped).")
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        sys.exit(1)


def cmd_restart(args):
    cmd_stop(args)
    cmd_serve(args)


def cmd_create_admin(args):
    cmd_create_user(args, default_role="admin")


def cmd_create_user(args, default_role: str | None = None):
    from web.auth import db as user_db
    from web.auth.password import hash_password

    role = getattr(args, "role", None) or default_role or "viewer"
    if role not in ("admin", "editor", "viewer"):
        print(f"Error: role must be one of admin/editor/viewer (got {role})", file=sys.stderr)
        sys.exit(1)

    user_db.init_db()
    existing = user_db.get_user_by_username(args.username)
    if existing:
        print(f"Error: user '{args.username}' already exists", file=sys.stderr)
        sys.exit(1)

    pwd_hash = hash_password(args.password)
    uid = user_db.create_user(args.username, pwd_hash, role)
    print(f"Created user '{args.username}' (id={uid}, role={role})")


def cmd_reindex(args):
    from web.indexer import db as index_db
    from web.indexer.scanner import full_scan
    from web.settings import get_settings

    index_db.init_db()
    settings = get_settings()
    result = full_scan(settings.paths.output_dir)
    print(f"Re-indexed {result['indexed']} files in {result['elapsed']:.1f}s")
    if result.get("errors"):
        print(f"Errors: {result['errors']}")


def main():
    p = argparse.ArgumentParser(description="AI Research Console")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Start the web server")
    sp.add_argument("--host", help="Override bind host")
    sp.add_argument("--port", type=int, help="Override bind port")
    sp.add_argument("--reload", action="store_true", help="Auto-reload on code change (dev)")
    sp.add_argument("--log-level", default="info")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("stop", help="Stop the running server")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("restart", help="Restart the server")
    sp.set_defaults(func=cmd_restart)

    sp = sub.add_parser("create-admin", help="Create an admin user")
    sp.add_argument("username")
    sp.add_argument("password")
    sp.set_defaults(func=cmd_create_admin)

    sp = sub.add_parser("create-user", help="Create a non-admin user")
    sp.add_argument("username")
    sp.add_argument("password")
    sp.add_argument("--role", choices=["admin", "editor", "viewer"], default="viewer")
    sp.set_defaults(func=cmd_create_user)

    sp = sub.add_parser("reindex", help="Force re-scan of output/ directory")
    sp.set_defaults(func=cmd_reindex)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
