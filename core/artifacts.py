"""Structured JSON artifacts derived from a completed intelligence round.

Alongside the 10 Markdown documents each round gets:

* ``action_items.json`` - immediate actions and this-week tests (from P9)
* ``watchlist.json``    - watchlist rows with trigger signals (from P9)
* ``sources.json``      - every source URL cited across the round, grouped
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

ACTION_FILE = "action_items.json"
WATCHLIST_FILE = "watchlist.json"
SOURCES_FILE = "sources.json"

SYNTHESIS_KEY = "09_executive_synthesis_and_actions"

_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'，。；：、）】》]+")
_TRAILING = ".,;:!?、，。；：）】》"

_ACTION_HEADERS = {
    "行动": "action",
    "action": "action",
    "为何现在": "why_now",
    "why now": "why_now",
    "关联雷达": "related_radars",
    "related radar": "related_radars",
    "related radars": "related_radars",
    "预期收益": "expected_benefit",
    "expected benefit": "expected_benefit",
    "工作量": "effort",
    "effort": "effort",
    "优先级": "priority",
    "priority": "priority",
}

_TEST_HEADERS = {
    "测试": "test",
    "test": "test",
    "方法": "method",
    "method": "method",
    "成功标准": "success_criteria",
    "success criteria": "success_criteria",
    "成本": "cost",
    "cost": "cost",
    "风险": "risk",
    "risk": "risk",
    "优先级": "priority",
    "priority": "priority",
}

_WATCH_HEADERS = {
    "议题": "topic",
    "topic": "topic",
    "观察点": "watch_point",
    "watch point": "watch_point",
    "触发信号": "trigger",
    "trigger": "trigger",
    "依据": "evidence",
    "evidence": "evidence",
}


def _find_section(text: str, *keywords: str) -> str:
    """Return the body of the first heading containing any keyword."""
    lines = text.splitlines()
    start: Optional[int] = None
    level = 2
    for i, line in enumerate(lines):
        match = re.match(r"^(#{2,3})\s+(.*)$", line)
        if not match:
            continue
        title = match.group(2).lower()
        if start is None:
            if any(k.lower() in title for k in keywords):
                start = i + 1
                level = len(match.group(1))
        elif len(match.group(1)) <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:]) if start is not None else ""


def _parse_table(section: str) -> list[dict]:
    """Parse the first Markdown table in a section into row dicts."""
    headers: Optional[list[str]] = None
    rows: list[dict] = []
    for raw in section.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        body = [c for c in cells if c]
        if body and all(re.fullmatch(r":?-{2,}:?", c) for c in body):
            continue
        if headers is None:
            headers = cells
            continue
        if len(cells) < len(headers):
            cells += [""] * (len(headers) - len(cells))
        rows.append(dict(zip(headers, cells[:len(headers)])))
    return rows


def _map_row(row: dict, mapping: dict) -> dict:
    mapped = {}
    for i, (key, value) in enumerate(row.items()):
        clean = key.strip().strip("*").strip().lower()
        name = mapping.get(clean)
        if not name:
            name = clean.replace(" ", "_") or f"col_{i + 1}"
        mapped[name] = value.strip()
    return mapped


def parse_action_items(p9_text: str) -> dict:
    """Extract immediate actions and this-week tests from a P9 report."""
    actions_section = _find_section(p9_text, "立即行动", "Immediate Actions")
    tests_section = _find_section(p9_text, "本周测试", "Test This Week")
    return {
        "actions": [_map_row(r, _ACTION_HEADERS) for r in _parse_table(actions_section)],
        "tests": [_map_row(r, _TEST_HEADERS) for r in _parse_table(tests_section)],
    }


def parse_watchlist(p9_text: str) -> list[dict]:
    """Extract watchlist rows from a P9 report."""
    section = _find_section(p9_text, "观察清单", "Watchlist")
    return [_map_row(r, _WATCH_HEADERS) for r in _parse_table(section)]


def extract_sources(text: str) -> list[str]:
    """Return unique URLs found in Markdown text, in first-seen order."""
    seen: dict[str, None] = {}
    for match in _URL_RE.findall(text):
        url = match.rstrip(_TRAILING)
        if url and url not in seen:
            seen[url] = None
    return list(seen)


def _write_json(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def export_round_artifacts(round_dir: str, date: Optional[str] = None) -> Optional[dict]:
    """Write action/watchlist/sources JSON next to a round's Markdown docs.

    Returns the written payloads keyed by filename, or ``None`` when the
    round directory does not exist.
    """
    if not os.path.isdir(round_dir):
        return None

    date = date or os.path.basename(round_dir.rstrip(os.sep))
    generated_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    documents = sorted(
        fn for fn in os.listdir(round_dir)
        if fn.endswith(".md") and os.path.isfile(os.path.join(round_dir, fn))
    )
    if not documents:
        return None

    p9_name = next((fn for fn in documents if fn.startswith("09_")), None)
    p9_text = ""
    if p9_name:
        with open(os.path.join(round_dir, p9_name), encoding="utf-8") as f:
            p9_text = f.read()

    parsed = parse_action_items(p9_text) if p9_text else {"actions": [], "tests": []}
    actions_payload = {
        "date": date,
        "generated_at": generated_at,
        "source_document": p9_name,
        "actions": parsed["actions"],
        "tests": parsed["tests"],
    }
    watchlist_payload = {
        "date": date,
        "generated_at": generated_at,
        "source_document": p9_name,
        "items": parse_watchlist(p9_text) if p9_text else [],
    }

    all_sources: dict[str, list[str]] = {}
    by_document: dict[str, int] = {}
    for fn in documents:
        with open(os.path.join(round_dir, fn), encoding="utf-8") as f:
            urls = extract_sources(f.read())
        by_document[fn] = len(urls)
        for url in urls:
            all_sources.setdefault(url, []).append(fn)

    domains: dict[str, int] = {}
    for url in all_sources:
        domain = re.sub(r"^https?://(www\.)?", "", url).split("/", 1)[0]
        domains[domain] = domains.get(domain, 0) + 1

    sources_payload = {
        "date": date,
        "generated_at": generated_at,
        "documents": documents,
        "total_sources": sum(by_document.values()),
        "total_unique": len(all_sources),
        "unique_domains": len(domains),
        "by_document": by_document,
        "domains": dict(sorted(domains.items(), key=lambda kv: -kv[1])),
        "sources": [
            {"url": url, "documents": docs, "count": len(docs)}
            for url, docs in all_sources.items()
        ],
    }

    written = {
        ACTION_FILE: actions_payload,
        WATCHLIST_FILE: watchlist_payload,
        SOURCES_FILE: sources_payload,
    }
    for filename, payload in written.items():
        _write_json(os.path.join(round_dir, filename), payload)
    logger.info(
        "Round %s artifacts written: %d actions, %d tests, %d watchlist items, %d sources",
        date, len(actions_payload["actions"]), len(actions_payload["tests"]),
        len(watchlist_payload["items"]), sources_payload["total_unique"],
    )
    return written
