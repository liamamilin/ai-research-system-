"""Pipeline round routes: status board + one-click run/cancel."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from core import artifacts, report_meta
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.runner import pipeline
from web.settings import get_settings

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


def _round_artifacts(date: str, output_dir: str) -> list[dict]:
    """Existing JSON artifacts of a round (name, size, and what they complain about).

    Reads only the artifacts themselves -- a few KB each -- because this runs
    for every round in the list. Re-parsing the round's markdown here would
    mean megabytes per page load, and the warnings are already recorded inside
    the files.
    """
    round_dir = os.path.join(output_dir, pipeline.PIPELINE_DIR, date)
    found = []
    for name in (artifacts.ACTION_FILE, artifacts.WATCHLIST_FILE, artifacts.SOURCES_FILE):
        path = os.path.join(round_dir, name)
        if not os.path.isfile(path):
            continue
        entry: dict = {"name": name, "size": os.path.getsize(path)}
        warnings, count = _artifact_complaints(path, name, round_dir)
        if warnings:
            entry["warnings"] = warnings
        if count == 0:
            entry["empty"] = True
        found.append(entry)
    return found


def _recorded_round(date: str, settings) -> Optional[dict]:
    """A round's recorded state, for rounds this server process did not run.

    ``pipeline.get_round_state`` reads the web runner's in-memory run dict, so
    it only knows about rounds started from the UI, and only until the process
    restarts. ``core.rounds`` keeps the same information durably in
    ``state/pipeline_rounds.json``, and both entry points write it: the web
    runner, and ``run.py --round-finish`` for a round run from a terminal or by
    launchd.

    Without this fallback a round started outside the UI showed no trigger, no
    timings and no per-stage status on the Rounds page -- the 2026-09-29 round
    displayed as a bare "10/10 完成" next to older rounds that showed "by
    launchd". That matters more now than it did: with the daily trigger gone,
    rounds get started by hand.
    """
    try:
        from core import rounds as rounds_store

        for entry in rounds_store.list_rounds(settings.paths.state_dir):
            if entry.get("date") == date:
                return entry
    except Exception:  # noqa: BLE001 - a missing record is not an error
        return None
    return None


def _artifact_complaints(path: str, name: str, round_dir: str) -> tuple[list[str], int]:
    """(warnings, item count) for one artifact, without parsing the documents."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return [], 0
    if not isinstance(payload, dict):
        return [], 0
    warnings = [str(w) for w in (payload.get("warnings") or [])]
    if name == artifacts.WATCHLIST_FILE:
        count = len(payload.get("items") or [])
        source = os.path.join(round_dir, "09_executive_synthesis_and_actions.md")
        # An empty watchlist next to a substantial P9 is a lost artifact, not a
        # quiet round. Say so rather than shipping a 285-byte button.
        if count == 0 and os.path.isfile(source) and os.path.getsize(source) > 4000:
            warnings.append("watchlist 为空，但本轮 P9 文档有实质内容，观察项很可能未能解析")
    elif name == artifacts.ACTION_FILE:
        count = len(payload.get("actions") or []) + len(payload.get("tests") or [])
    else:
        count = len(payload.get("sources") or [])
    return warnings, count


def _round_payload(date: str, with_warnings: bool = False) -> dict:
    settings = get_settings()
    stages = pipeline.build_round_files(date, settings.paths.output_dir)
    tokens = report_meta.round_tokens(date)
    for stage in stages:
        stage["tokens"] = tokens.get(stage["key"], 0)
    done = sum(1 for s in stages if s["exists"])
    payload = {
        "date": date,
        "done": done,
        "total": len(stages),
        "tokens_total": sum(tokens.values()),
        "stages": stages,
        "artifacts": _round_artifacts(date, settings.paths.output_dir),
        "live": pipeline.get_round_state(date) or _recorded_round(date, settings),
    }
    if with_warnings:
        payload["warnings"] = _round_artifact_warnings(
            date, settings.paths.output_dir)
    return payload


def _round_artifact_warnings(date: str, output_dir: str) -> list[str]:
    """What the round's own artifacts say about themselves.

    A round can finish 10/10 and still have lost its watchlist: on 2026-09-26
    P9 wrote 11 watchlist items as a numbered list, the parser only accepted
    tables, and the artifact came out empty. The warning existed in the JSON
    the whole time and reached no screen, so the page showed a healthy round
    with a 285-byte watchlist button on it. Off by default because it re-reads
    every document in the round.
    """
    round_dir = os.path.join(output_dir, pipeline.PIPELINE_DIR, date)
    payloads = artifacts.load_round_payloads(round_dir)
    if not payloads:
        return []
    warnings: list[str] = []
    for section in ("actions", "watchlist"):
        for warning in (payloads.get(section) or {}).get("warnings") or []:
            text = str(warning)
            if text not in warnings:
                warnings.append(text)
    return warnings


@router.get("/rounds")
def list_rounds(
    limit: int = Query(14, ge=1, le=120),
    user=Depends(require_viewer),
):
    """List recent rounds with per-stage output status."""
    settings = get_settings()
    base = os.path.join(settings.paths.output_dir, pipeline.PIPELINE_DIR)

    dates: list[str] = []
    if os.path.isdir(base):
        dates = sorted(
            (d for d in os.listdir(base)
             if os.path.isdir(os.path.join(base, d)) and len(d) == 10),
            reverse=True,
        )

    # Make sure a currently running round is always visible
    for state in pipeline.list_round_states():
        if state["status"] == "running" and state["date"] not in dates:
            dates.insert(0, state["date"])

    rounds = [_round_payload(d) for d in dates[:limit]]
    running = any(
        r["live"] and r["live"]["status"] == "running" for r in rounds
    )
    return {
        "rounds": rounds,
        "running": running,
        "groups": [{"name": name, "stages": keys} for name, keys in pipeline.GROUPS],
    }


@router.get("/rounds/{date}")
def get_round(date: str, user=Depends(require_viewer)):
    if len(date) != 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_date", "日期格式应为 YYYY-MM-DD"),
        )
    return _round_payload(date, with_warnings=True)


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _require_date(value: str | None, field: str) -> str | None:
    """Validate a real YYYY-MM-DD date before it is joined onto a path."""
    if value is None:
        return None
    if not isinstance(value, str) or not _DATE_RE.match(value):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_date", f"{field} 格式应为 YYYY-MM-DD"),
        )
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_date", f"{field} 不是有效日期"),
        )
    return value


@router.get("/rounds/{date}/diff")
def get_round_diff(date: str, against: str | None = Query(None),
                   user=Depends(require_viewer)):
    """Content diff between a round and the previous one (or ``against``)."""
    _require_date(date, "date")
    settings = get_settings()
    base = os.path.join(settings.paths.output_dir, pipeline.PIPELINE_DIR)
    round_b = os.path.join(base, date)
    if not os.path.isdir(round_b):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("round_not_found", "轮次不存在"),
        )

    if against:
        previous = _require_date(against, "against")
    else:
        candidates = sorted(
            d for d in os.listdir(base)
            if len(d) == 10 and os.path.isdir(os.path.join(base, d)) and d < date
        )
        previous = candidates[-1] if candidates else None

    if not previous:
        return {"from": None, "to": date, "actions": {"added": [], "removed": [], "persisted": []},
                "tests": {"added": [], "removed": [], "persisted": []},
                "watchlist": {"added": [], "removed": [], "persisted": []},
                "sources": {"added": [], "removed": [], "new_domains": []},
                "counts": {"actions_added": 0, "actions_removed": 0, "tests_added": 0,
                           "watchlist_added": 0, "watchlist_removed": 0,
                           "sources_added": 0, "sources_removed": 0}}

    diff = artifacts.diff_rounds(os.path.join(base, previous), round_b,
                                 date_a=previous, date_b=date)
    if diff is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("round_not_found", "对比轮次不存在"),
        )
    return diff


@router.post("/run")
def run_round(request: Request, payload: dict | None = None,
              user=Depends(require_editor)):
    """Start today's round in the background."""
    settings = get_settings()
    payload = payload or {}
    try:
        concurrency = int(payload.get("concurrency", 3))
    except (TypeError, ValueError):
        concurrency = 3

    try:
        state = pipeline.start_round(
            config_dir=settings.paths.config_dir,
            jobs_dir=settings.paths.jobs_dir,
            concurrency=concurrency,
            trigger=user["username"],
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("already_running", str(e)),
        )

    audit.log("pipeline_run", user=user["username"], target=state["date"],
              result="started",
              ip=request.client.host if request.client else None)
    return state


@router.post("/retry")
def retry_round(request: Request, payload: dict | None = None,
                invalidate_downstream: bool = Query(
                    True, description="also re-run stages that consumed a re-run stage"),
                user=Depends(require_editor)):
    """Re-run the unfinished stages of today's (or a given) round.

    By default every stage that consumed the output of a re-run stage is
    invalidated and re-run as well, so the round cannot report success while
    its synthesis still reflects stale upstream documents.
    """
    settings = get_settings()
    payload = payload or {}
    try:
        concurrency = int(payload.get("concurrency", 3))
    except (TypeError, ValueError):
        concurrency = 3
    date = _require_date(payload.get("date"), "date")

    try:
        state = pipeline.start_retry(
            config_dir=settings.paths.config_dir,
            jobs_dir=settings.paths.jobs_dir,
            output_dir=settings.paths.output_dir,
            concurrency=concurrency,
            trigger=user["username"],
            date=date,
            invalidate_downstream=invalidate_downstream,
        )
    except RuntimeError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=ApiError.make("retry_unavailable", str(e)),
        )

    audit.log("pipeline_retry", user=user["username"], target=state["date"],
              result="started",
              ip=request.client.host if request.client else None)
    return state


@router.post("/cancel")
def cancel_round(request: Request, user=Depends(require_editor)):
    """Cancel the running round (stops in-flight stages)."""
    if not pipeline.cancel_round():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_running", "当前没有正在运行的轮次"),
        )
    audit.log("pipeline_cancel", user=user["username"], result="cancelled",
              ip=request.client.host if request.client else None)
    return {"ok": True}
