#!/usr/bin/env python3
"""Deprecated: use ``scripts/maintenance.py``.

This script only globbed ``logs/*.log*``, so it could never reach
logs/schedules, logs/jobs or logs/openai, where most of the volume was -- and
nothing invoked it anyway. Kept as a thin shim so anything that still calls it
gets the real implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.maintenance import prune_logs  # noqa: E402


def cleanup_logs(logs_dir: str = "logs", days: int = 7) -> None:
    print(f"已合并到 scripts/maintenance.py；转发到 prune_logs（保留 {days} 天）")
    print(prune_logs(Path(logs_dir), days, dry_run=False))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="（已废弃）请改用 scripts/maintenance.py")
    parser.add_argument("--dir", default="logs")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    cleanup_logs(args.dir, args.days)
