#!/usr/bin/env python3
"""Export structured JSON artifacts (actions / watchlist / sources) for rounds.

Usage:
  python scripts/export_round_artifacts.py                # latest round
  python scripts/export_round_artifacts.py 2026-09-19     # one round
  python scripts/export_round_artifacts.py --all          # every round
  python scripts/export_round_artifacts.py --output-dir output
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.artifacts import SYNTHESIS_KEY, export_round_artifacts  # noqa: E402

PIPELINE_DIR = "practical_ai_intelligence"


def _round_dates(output_dir: str) -> list[str]:
    root = os.path.join(output_dir, PIPELINE_DIR)
    if not os.path.isdir(root):
        return []
    return sorted(
        d for d in os.listdir(root)
        if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("date", nargs="?",
                        help="Round date (YYYY-MM-DD); default: latest")
    parser.add_argument("--all", action="store_true", help="Export every round")
    parser.add_argument("--output-dir", default="output",
                        help="Reports root (default: output)")
    args = parser.parse_args()

    dates = _round_dates(args.output_dir)
    if not dates:
        print(f"  no rounds found under {args.output_dir}/{PIPELINE_DIR}")
        return 1

    if args.all:
        targets = dates
    elif args.date:
        if args.date not in dates:
            print(f"  round not found: {args.date} (have: {', '.join(dates)})")
            return 1
        targets = [args.date]
    else:
        targets = [dates[-1]]

    failures = 0
    for date in targets:
        round_dir = os.path.join(args.output_dir, PIPELINE_DIR, date)
        written = export_round_artifacts(round_dir, date=date)
        if not written:
            print(f"  {date}: skipped (no Markdown documents)")
            failures += 1
            continue
        actions = len(written["action_items.json"]["actions"])
        tests = len(written["action_items.json"]["tests"])
        watch = len(written["watchlist.json"]["items"])
        sources = written["sources.json"]["total_unique"]
        has_p9 = os.path.exists(os.path.join(round_dir, f"{SYNTHESIS_KEY}.md"))
        print(f"  ✓ {date}: {actions} actions, {tests} tests, "
              f"{watch} watchlist, {sources} sources"
              + ("" if has_p9 else " (no P9 yet)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
