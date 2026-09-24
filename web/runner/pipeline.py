"""Pipeline round orchestration.

Runs the ``practical_ai_intelligence`` 10-stage intelligence round
(P0 -> P1-P6 -> P7-P8 -> P9) in-process, reusing the web job runner so each
stage streams over SSE and records state/usage like a normal job run.

Dependency groups (matching scripts/run_practical_intelligence.sh):
    P0      collection planner
    P1-P6   radar collection (parallel)
    P7-P8   analysis (depends on P1-P6 files)
    P9      synthesis (depends on P7+P8 files)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

from core.budget import pipeline_allowed
from core.engine import ResearchEngine
from web.runner.executor import run_job_in_thread
from web.runner.registry import TaskRegistry

logger = logging.getLogger("ai_research.web.pipeline")

PIPELINE_DIR = "practical_ai_intelligence"

# (job basename, output filename, UI label)
STAGES: list[tuple[str, str, str]] = [
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

GROUPS: list[tuple[str, list[str]]] = [
    ("P0", ["00_collection_planner"]),
    ("P1-P6", [
        "01_model_and_pricing_radar",
        "02_ai_coding_tools_radar",
        "03_agent_workflow_radar",
        "04_project_understanding_radar",
        "05_context_rag_memory_radar",
        "06_infra_and_eval_radar",
    ]),
    ("P7-P8", [
        "07_product_content_opportunities",
        "08_risk_and_alternatives",
    ]),
    ("P9", ["09_executive_synthesis_and_actions"]),
]

_STAGE_FILE = {key: fname for key, fname, _ in STAGES}
_STAGE_LABEL = {key: label for key, _, label in STAGES}

_rounds: dict[str, dict] = {}
_lock = threading.RLock()
_persist_lock = threading.RLock()

# Persisted round states (survives server restarts)
_STATE_FILE = os.path.join("state", "pipeline_rounds.json")
_KEEP_ROUNDS = 20


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _serialize(state: dict) -> dict:
    """Internal state -> JSON-safe public dict."""
    stages = [
        {k: v for k, v in stage.items() if not k.startswith("_")}
        for stage in state["stages"].values()
    ]
    return {
        "date": state["date"],
        "status": state["status"],
        "trigger": state.get("trigger", ""),
        "started_at": state.get("started_at"),
        "finished_at": state.get("finished_at"),
        "stages": stages,
    }


def _deserialize(data: dict) -> dict:
    """Public dict -> internal state."""
    stages = {}
    for stage in data.get("stages", []):
        key = stage.get("key", "")
        if not key:
            continue
        stages[key] = dict(stage)
    return {
        "date": data["date"],
        "status": data.get("status", "unknown"),
        "trigger": data.get("trigger", ""),
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
        "cancel_requested": False,
        "stages": stages,
    }


def _persist(state: dict) -> None:
    """Write one round state to disk (atomic, capped history). Never raises.

    Delegates to ``core.rounds`` so cron-driven rounds recorded by the CLI
    live in the same file and the Rounds page shows both.
    """
    try:
        from core.rounds import upsert_round

        upsert_round(
            state["date"],
            status=state["status"],
            trigger=state.get("trigger", ""),
            stages=_serialize(state)["stages"],
            started_at=state.get("started_at"),
            finished_at=state.get("finished_at"),
        )
    except Exception as exc:  # noqa: BLE001 - persistence must never break runs
        logger.warning("Failed to persist round state: %s", exc)


def load_persisted_rounds() -> int:
    """Load persisted rounds into memory (called at server startup).

    Rounds that were 'running' when the process died are marked
    'interrupted'. Returns the number of rounds loaded.
    """
    try:
        from core.rounds import list_rounds, mark_interrupted

        mark_interrupted()
        persisted = list_rounds()
    except Exception as exc:  # noqa: BLE001 - never block startup
        logger.warning("Failed to load persisted rounds: %s", exc)
        return 0

    with _lock:
        for payload in persisted:
            date = payload.get("date")
            if not date or date in _rounds:
                continue
            _rounds[date] = _deserialize(payload)
    return len(persisted)


# ---------------------------------------------------------------------------
# State access
# ---------------------------------------------------------------------------


def list_round_states() -> list[dict]:
    """Return in-memory round run states (most recent first)."""
    with _lock:
        return sorted(_rounds.values(), key=lambda r: r["date"], reverse=True)


def get_round_state(date: str) -> Optional[dict]:
    """Return the public (serializable) run state for a round date."""
    with _lock:
        state = _rounds.get(date)
    return _public_state(state) if state else None


def cancel_round() -> bool:
    """Request cancellation of the running round. Returns True if signalled."""
    with _lock:
        for state in _rounds.values():
            if state["status"] == "running":
                state["cancel_requested"] = True
                break
        else:
            return False

    registry = TaskRegistry()
    for job_name in [f"{PIPELINE_DIR}/{key}" for key, _, _ in STAGES]:
        registry.cancel(job_name)
    return True


# ---------------------------------------------------------------------------
# Round execution
# ---------------------------------------------------------------------------


def start_round(config_dir: str, jobs_dir: str, concurrency: int = 3,
                trigger: str = "web") -> dict:
    """Start today's round in a background thread. Raises if one is running."""
    allowed, reason = pipeline_allowed()
    if not allowed:
        raise RuntimeError(reason)

    date = datetime.now().strftime("%Y-%m-%d")

    with _lock:
        for state in _rounds.values():
            if state["status"] == "running":
                raise RuntimeError("已有一轮正在运行")

        state = {
            "date": date,
            "status": "running",
            "trigger": trigger,
            "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "finished_at": None,
            "cancel_requested": False,
            "stages": {
                key: {
                    "key": key,
                    "label": _STAGE_LABEL[key],
                    "file": _STAGE_FILE[key],
                    "status": "pending",
                    "group": next(
                        g for g, keys in GROUPS if key in keys
                    ),
                }
                for key, _, _ in STAGES
            },
        }
        _rounds[date] = state
    _persist(state)

    thread = threading.Thread(
        target=_run_round,
        args=(state, config_dir, jobs_dir, max(1, min(int(concurrency), 6))),
        daemon=True,
        name=f"pipeline-{date}",
    )
    thread.start()
    logger.info("Round %s started (concurrency=%d, trigger=%s)",
                date, concurrency, trigger)
    return _public_state(state)


def _notify_round(state: dict, results: dict[str, str],
                  config_dir: str = "config") -> None:
    """Best-effort round summary notification (webhook)."""
    try:
        from core.config import load_system_config
        from core.notify import send

        failed = [k for k, v in results.items() if v != "success"]
        summary = f"状态 {state['status']} · 完成 {len(results) - len(failed)}/{len(results)}"
        if failed:
            summary += " · 失败: " + ", ".join(failed)

        message = summary
        try:
            sys_cfg = load_system_config(config_dir)
            digest_cfg = (sys_cfg.get("notifications") or {}).get("digest") or {}
            if digest_cfg.get("enabled", True):
                from core.digest import digest_for_round

                output_dir = (sys_cfg.get("defaults") or {}).get(
                    "output_dir", "output")
                digest = digest_for_round(
                    state["date"], output_dir,
                    pipeline_dir=PIPELINE_DIR,
                    results=results,
                    max_actions=int(digest_cfg.get("max_actions", 5)),
                )
                if digest:
                    message = digest["text"]
        except Exception as exc:  # noqa: BLE001 - digest is optional
            logger.debug("Round digest skipped: %s", exc)

        send(
            "round_finished",
            f"情报轮次 {state['date']}",
            message,
            fields={
                "date": state["date"],
                "status": state["status"],
                "failed_stages": failed,
            },
        )
    except Exception as exc:  # noqa: BLE001 - notifications never break runs
        logger.debug("Round notification skipped: %s", exc)


def _export_round_artifacts(state: dict, config_dir: str) -> None:
    """Best-effort JSON artifacts (actions, watchlist, sources) for a round."""
    try:
        from core.artifacts import export_round_artifacts
        from core.config import load_system_config

        output_dir = (load_system_config(config_dir).get("defaults") or {}).get(
            "output_dir", "output")
        round_dir = os.path.join(output_dir, PIPELINE_DIR, state["date"])
        written = export_round_artifacts(round_dir, date=state["date"])
        if written:
            logger.info("Round %s artifacts exported", state["date"])
            _sync_round_tracking(state["date"], written)
    except Exception as exc:  # noqa: BLE001 - artifacts are best-effort
        logger.warning("Round %s artifact export failed: %s", state["date"], exc)


def _sync_round_tracking(date: str, written: dict) -> None:
    """Feed exported artifacts into the tracking store (best-effort)."""
    try:
        from core import tracking
        from web.settings import get_settings

        tracking.use_state_dir(get_settings().paths.state_dir)
        actions_payload = written.get("action_items.json") or {}
        watchlist_payload = written.get("watchlist.json") or {}
        counts = tracking.sync_round(
            date,
            actions_payload.get("actions"),
            actions_payload.get("tests"),
            watchlist_payload.get("items"),
        )
        logger.info("Round %s tracking synced (%d new, %d updated)",
                    date, counts["new"], counts["updated"])
    except Exception as exc:  # noqa: BLE001 - tracking is best-effort
        logger.warning("Round %s tracking sync failed: %s", date, exc)


def _stages_needing_rerun(file_stages: dict, previous: dict) -> list[str]:
    """Stages to re-run: output missing, or last attempt truly failed.

    "skipped" means "already complete in an earlier retry" and must NOT
    trigger a rerun (regression guard).
    """
    rerun_statuses = {"failed", "cancelled", "interrupted"}
    needed: list[str] = []
    for key, _, _ in STAGES:
        status = (previous.get(key) or {}).get("status", "")
        if (not file_stages[key]["exists"]) or status in rerun_statuses:
            needed.append(key)
    return needed


def start_retry(config_dir: str, jobs_dir: str, output_dir: str,
                concurrency: int = 3, trigger: str = "web",
                date: Optional[str] = None) -> dict:
    """Re-run the unfinished stages of a round (missing/failed/interrupted).

    Dependency groups are preserved; completed stages are marked ``skipped``.
    """
    date = date or datetime.now().strftime("%Y-%m-%d")

    file_stages = {s["key"]: s for s in build_round_files(date, output_dir)}

    with _lock:
        for existing in _rounds.values():
            if existing["status"] == "running":
                raise RuntimeError("已有一轮正在运行")

        previous = _rounds.get(date, {}).get("stages", {})
        needed = _stages_needing_rerun(file_stages, previous)

        if not needed:
            raise RuntimeError("该轮次已全部完成，无需补跑")

        state = {
            "date": date,
            "status": "running",
            "trigger": trigger,
            "started_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "finished_at": None,
            "cancel_requested": False,
            "stages": {
                key: {
                    "key": key,
                    "label": _STAGE_LABEL[key],
                    "file": _STAGE_FILE[key],
                    "status": "pending" if key in needed else "skipped",
                    "group": next(g for g, keys in GROUPS if key in keys),
                }
                for key, _, _ in STAGES
            },
        }
        _rounds[date] = state
    _persist(state)

    thread = threading.Thread(
        target=_run_round,
        args=(state, config_dir, jobs_dir,
              max(1, min(int(concurrency), 6)), set(needed)),
        daemon=True,
        name=f"pipeline-retry-{date}",
    )
    thread.start()
    logger.info("Round %s retry started for %d stage(s): %s",
                date, len(needed), ", ".join(k[:2] for k in needed))
    return _public_state(state)


def _public_state(state: dict) -> dict:
    with _lock:
        return {
            "date": state["date"],
            "status": state["status"],
            "trigger": state["trigger"],
            "started_at": state["started_at"],
            "finished_at": state["finished_at"],
            "stages": list(state["stages"].values()),
        }


def _set_stage(state: dict, key: str, status: str, **extra):
    with _lock:
        stage = state["stages"][key]
        stage["status"] = status
        stage.update(extra)
    _persist(state)


def _run_stage(state: dict, key: str, task, config_dir: str, jobs_dir: str):
    """Run one stage. Marks it 'running' only when the worker picks it up."""
    _set_stage(state, key, "running")
    run_job_in_thread(task, config_dir, jobs_dir, False,
                      date_override=state["date"])


def _cancelled(state: dict) -> bool:
    with _lock:
        return bool(state["cancel_requested"])


def _run_round(state: dict, config_dir: str, jobs_dir: str, concurrency: int,
               stage_filter: Optional[set[str]] = None):
    registry = TaskRegistry()
    workspace_dir = None
    results: dict[str, str] = {}

    try:
        for group_name, stage_keys in GROUPS:
            active = [k for k in stage_keys
                      if stage_filter is None or k in stage_filter]
            if not active:
                continue

            if _cancelled(state):
                for key in active:
                    _set_stage(state, key, "cancelled")
                continue

            logger.info("Round %s: group %s (%d stages)",
                        state["date"], group_name, len(active))
            with ThreadPoolExecutor(
                max_workers=min(concurrency, len(active)),
                thread_name_prefix=f"round-{group_name}",
            ) as pool:
                futures = {}
                for key in active:
                    job_name = f"{PIPELINE_DIR}/{key}"
                    _set_stage(state, key, "queued")

                    engine_stub = ResearchEngine(
                        config_dir=config_dir, jobs_dir=jobs_dir,
                        workspace_dir=workspace_dir,
                    )
                    task = registry.start(job_name, "pipeline", engine_stub)
                    future = pool.submit(
                        _run_stage, state, key, task, config_dir, jobs_dir
                    )
                    task.future = future
                    futures[future] = (key, task)

                for future in futures:
                    key, task = futures[future]
                    try:
                        future.result()
                        status = task.status
                    except Exception as exc:  # noqa: BLE001 - recorded per stage
                        logger.exception("Stage %s crashed", key)
                        status = "failed"
                        _set_stage(state, key, status, error=str(exc)[:300])
                        results[key] = status
                        continue
                    results[key] = status
                    if status != "running":
                        _set_stage(state, key, status)

            if _cancelled(state):
                logger.info("Round %s cancellation requested", state["date"])

        with _lock:
            failed = [k for k, s in results.items() if s != "success"]
            if state["cancel_requested"]:
                state["status"] = "cancelled"
            elif not failed:
                state["status"] = "success"
            elif len(failed) == len(results):
                state["status"] = "failed"
            else:
                state["status"] = "partial"
            state["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

        _persist(state)
        _export_round_artifacts(state, config_dir)
        _notify_round(state, results, config_dir)
        logger.info("Round %s finished: %s (failed: %s)",
                    state["date"], state["status"], ", ".join(failed) or "none")
    except Exception as exc:  # noqa: BLE001 - never kill the server
        logger.exception("Round %s crashed", state["date"])
        with _lock:
            state["status"] = "failed"
            state["finished_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        _persist(state)


# ---------------------------------------------------------------------------
# Output inspection
# ---------------------------------------------------------------------------


def build_round_files(date: str, output_root: str) -> list[dict]:
    """Return per-stage output file metadata for a given round date."""
    import os

    round_dir = os.path.join(output_root, PIPELINE_DIR, date)
    live = get_round_state(date)
    live_stages = {s["key"]: s for s in (live or {}).get("stages", [])}

    stages = []
    for key, filename, label in STAGES:
        full = os.path.join(round_dir, filename)
        exists = os.path.isfile(full)
        info = {
            "key": key,
            "label": label,
            "file": f"{PIPELINE_DIR}/{date}/{filename}",
            "exists": exists,
            "size": os.path.getsize(full) if exists else 0,
            "mtime": os.path.getmtime(full) if exists else None,
            "status": live_stages.get(key, {}).get("status")
            or ("done" if exists else "missing"),
        }
        stages.append(info)
    return stages
