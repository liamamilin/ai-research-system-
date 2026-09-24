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

# (stage key, output filename, label) — mirrors web/runner/pipeline.STAGES so
# CLI/cron rounds can be scored from the files they produced.
_PIPELINE_STAGES = [
    ("00_collection_planner", "00_collection_plan.md", "P0 采集计划"),
    ("01_model_and_pricing_radar", "01_model_and_pricing_radar.md", "P1 模型定价"),
    ("02_ai_coding_tools_radar", "02_ai_coding_tools_radar.md", "P2 编程工具"),
    ("03_agent_workflow_radar", "03_agent_workflow_radar.md", "P3 Agent 工作流"),
    ("04_project_understanding_radar", "04_project_understanding_radar.md", "P4 项目理解"),
    ("05_context_rag_memory_radar", "05_context_rag_memory_radar.md", "P5 上下文/RAG"),
    ("06_infra_and_eval_radar", "06_infra_and_eval_radar.md", "P6 基础设施/评测"),
    ("07_product_content_opportunities", "07_product_content_opportunities.md", "P7 产品内容机会"),
    ("08_risk_and_alternatives", "08_risk_and_alternatives.md", "P8 风险与替代"),
    ("09_executive_synthesis_and_actions", "09_executive_synthesis_and_actions.md", "P9 执行综合"),
]


def rounds_now() -> str:
    from core.rounds import now_iso

    return now_iso()


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

    # Record the round so the Rounds page and health checks see CLI/cron runs.
    try:
        from .rounds import infer_stage_status, upsert_round

        stage_files = {}
        for stage in _PIPELINE_STAGES:
            stage_files[stage[0]] = stage[1]
        stages = infer_stage_status(round_dir, stage_files)
        ok = sum(1 for s in stages if s["status"] == "success")
        if not stages:
            status = "partial" if written else "failed"
        elif ok == len(stages):
            status = "success"
        elif ok == 0:
            status = "failed"
        else:
            status = "partial"
        entry = upsert_round(
            date, status=status, trigger="cron", stages=stages,
            finished_at=rounds_now(), state_dir=state_dir,
        )
        summary["round"] = {
            "status": status,
            "stages_ok": ok,
            "stages_total": len(stages),
            "recorded": bool(entry),
        }
    except Exception as exc:  # noqa: BLE001 - bookkeeping must not break finishing
        logger.warning("Failed to record round state: %s", exc)

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
