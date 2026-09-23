"""Round completion hooks for CLI/cron runs.

The web pipeline runner exports artifacts, syncs the tracking store and
sends the digest notification when a round finishes. The cron path
(``scripts/run_practical_intelligence.sh``) runs plain jobs, so this
module provides the same finishing steps for ``run.py --round-finish``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

PIPELINE_DIR = "practical_ai_intelligence"


def finish_round(date: str, output_dir: str = "output", state_dir: str = "state",
                 config_dir: str = "config",
                 results: Optional[dict] = None,
                 notify: bool = True) -> dict:
    """Export artifacts, sync tracking and optionally send the digest."""
    from .artifacts import export_round_artifacts
    from .tracking import sync_round, use_state_dir

    round_dir = os.path.join(output_dir, PIPELINE_DIR, date)
    summary: dict = {"date": date, "round_dir": round_dir,
                     "artifacts": False, "tracking": None, "notified": False,
                     "warnings": []}

    written = export_round_artifacts(round_dir, date=date)
    if written:
        summary["artifacts"] = True
        try:
            use_state_dir(state_dir)
            counts = sync_round(
                date,
                written["action_items.json"]["actions"],
                written["action_items.json"]["tests"],
                written["watchlist.json"]["items"],
            )
            summary["tracking"] = counts
        except Exception as exc:  # noqa: BLE001 - tracking is best-effort
            logger.warning("Round tracking sync failed: %s", exc)
    else:
        logger.warning("Round %s has no documents; artifacts not exported", date)

    for filename, payload in (written or {}).items():
        for warning in (payload or {}).get("warnings", []):
            summary.setdefault("warnings", []).append(f"{filename}: {warning}")

    if notify:
        try:
            from .config import load_system_config
            from .digest import build_digest
            from .notify import send

            sys_config = load_system_config(config_dir)
            digest = build_digest(date, round_dir, results=results)
            if digest:
                summary["notified"] = bool(send(
                    "round_finished", digest["title"], digest["text"],
                    fields={"date": date},
                    sys_config=sys_config,
                ))
        except Exception as exc:  # noqa: BLE001 - notifications never break runs
            logger.warning("Round finish notification failed: %s", exc)

    return summary
