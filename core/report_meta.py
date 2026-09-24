"""Sidecar metadata for generated reports.

The engine appends one JSON line per saved report to
``state/report_meta.jsonl``; the web layer reads it to enrich report lists
and to power round comparisons.

Record::

    {
      "ts": "2026-09-19T01:14:22+0800",
      "path": "output/practical_ai_intelligence/2026-09-19/06_x.md",
      "rel":  "practical_ai_intelligence/2026-09-19/06_x.md",
      "job":  "practical_ai_intelligence/06_infra_and_eval_radar",
      "model": "deepseek-v4.1-flash",
      "prompt_tokens": 123, "completion_tokens": 456, "total_tokens": 579,
      "searches": 12, "duration_seconds": 151.3,
      "round_date": "2026-09-19"
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

META_DIR = "state"
META_FILE = os.path.join(META_DIR, "report_meta.jsonl")

_ROUND_DATE_RE = re.compile(r"practical_ai_intelligence/(\d{4}-\d{2}-\d{2})/")

_write_lock = threading.Lock()
_cache: dict = {"mtime": 0.0, "map": {}}


def normalize_rel(path: str) -> str:
    """Normalize a report path to be relative to the output directory."""
    p = (path or "").replace("\\", "/").lstrip("/")
    if p.startswith("output/"):
        p = p[len("output/"):]
    return p


def append_record(
    path: str,
    job: str,
    usage: Optional[dict] = None,
    duration_seconds: Optional[float] = None,
    extra: Optional[dict] = None,
) -> None:
    """Append a metadata record for a saved report (best-effort)."""
    usage = usage or {}
    rel = normalize_rel(path)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "path": path,
        "rel": rel,
        "job": job,
        "model": usage.get("model"),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "searches": usage.get("searches"),
        "duration_seconds": duration_seconds,
    }
    if extra:
        record.update({k: v for k, v in extra.items() if v is not None})
    match = _ROUND_DATE_RE.search(rel)
    if match:
        record["round_date"] = match.group(1)

    try:
        with _write_lock:
            os.makedirs(META_DIR, exist_ok=True)
            with open(META_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.debug("report_meta append failed: %s", exc)


def load_all() -> dict:
    """Return ``{rel_path: record}`` (last record wins), cached by mtime."""
    try:
        mtime = os.path.getmtime(META_FILE)
    except OSError:
        _cache["mtime"] = 0.0
        _cache["map"] = {}
        return {}

    if mtime == _cache["mtime"] and _cache["map"]:
        return _cache["map"]

    records: dict = {}
    try:
        with open(META_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rel = record.get("rel") or normalize_rel(record.get("path", ""))
                if rel:
                    record["rel"] = rel
                    records[rel] = record
    except OSError:
        return {}

    _cache["mtime"] = mtime
    _cache["map"] = records
    return records


def get(rel_path: str) -> Optional[dict]:
    return load_all().get(normalize_rel(rel_path))


def round_tokens(round_date: str) -> dict:
    """Aggregate ``{stage_key: total_tokens}`` for a pipeline round date."""
    totals: dict = {}
    for record in load_all().values():
        if record.get("round_date") != round_date:
            continue
        job = record.get("job") or ""
        stage = job.split("/")[-1]
        totals[stage] = totals.get(stage, 0) + int(record.get("total_tokens") or 0)
    return totals
