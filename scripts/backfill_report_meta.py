#!/usr/bin/env python3
"""Backfill state/report_meta.jsonl from state/history/*.jsonl.

Run once after upgrading: existing reports have usage data in the job history
but no sidecar metadata. Idempotent: reports already present in the meta file
are left untouched.
"""

from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import report_meta  # noqa: E402


def main() -> int:
    existing = report_meta.load_all()
    added = 0

    for path in sorted(glob.glob(os.path.join("state", "history", "*.jsonl"))):
        # Latest successful run per report path
        latest: dict[str, dict] = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("status") != "success":
                        continue
                    output = entry.get("output_path")
                    if not output:
                        continue
                    rel = report_meta.normalize_rel(output)
                    previous = latest.get(rel)
                    if previous is None or entry.get("ts", "") > previous.get("ts", ""):
                        latest[rel] = entry
        except OSError:
            continue

        for rel, entry in latest.items():
            if rel in existing:
                continue
            report_meta.append_record(
                entry.get("output_path", rel),
                entry.get("job_name", ""),
                usage=entry.get("usage"),
                duration_seconds=entry.get("duration_seconds"),
            )
            added += 1

    print(f"Backfilled {added} report metadata record(s); "
          f"{len(report_meta.load_all())} total.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
