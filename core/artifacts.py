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

SCHEMA_VERSION = 2

SYNTHESIS_KEY = "09_executive_synthesis_and_actions"

_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'，。；：、）】》]+")
_TRAILING = ".,;:!?、，。；：）】》"

# Canonical field aliases. Header wording drifts between rounds and models,
# so every known spelling maps to one canonical name; anything unseen falls
# back to keyword matching and is reported as a warning instead of dropped.
_FIELD_ALIASES: dict[str, list[str]] = {
    "action": [
        "行动", "立即行动", "行动项", "具体行动", "待办", "任务", "下一步",
        "action", "actionitem", "actionitems", "immediateaction", "todo", "nextaction",
    ],
    "why_now": [
        "为何现在", "为什么是现在", "为什么现在", "为何是现在", "现在的原因", "时机",
        "whynow", "whyitnow", "rationale", "timing",
    ],
    "related_radars": [
        "关联雷达", "相关雷达", "来源雷达", "关联报告",
        "relatedradar", "relatedradars", "relatedreports",
    ],
    "expected_benefit": [
        "预期收益", "收益", "价值", "好处", "expectedbenefit", "benefit", "value",
    ],
    "effort": [
        "工作量", "投入", "成本", "effort", "workload", "cost",
    ],
    "priority": [
        "优先级", "优先", "priority", "prio",
    ],
    "owner_role": [
        "负责人", "所需角色", "角色", "承担者", "owner", "role", "responsible", "assignee",
    ],
    "deadline": [
        "时限", "截止", "截止时间", "期限", "完成时间",
        "deadline", "duedate", "due", "when", "timeline",
    ],
    "acceptance": [
        "验收标准", "验收", "完成标准", "acceptance", "acceptancecriteria", "definitionofdone",
    ],
    "test": [
        "测试", "试验", "验证", "test", "tests", "experiment", "trial",
    ],
    "method": [
        "方法", "步骤", "做法", "method", "approach", "steps", "how",
    ],
    "success_criteria": [
        "成功标准", "判定标准", "通过标准", "successcriteria", "passcriteria", "criteria",
    ],
    "risk": [
        "风险", "risk", "risks",
    ],
    "topic": [
        "议题", "观察项", "主题", "关注点", "观察议题",
        "topic", "topics", "watchitem", "subject", "item",
    ],
    "watch_point": [
        "观察点", "观察要点", "watchpoint", "watchitemdetail",
    ],
    "watch_rationale": [
        "为什么观察", "为何观察", "观察理由", "理由", "原因",
        "whys", "reason", "why",
    ],
    "trigger": [
        "触发信号", "触发条件", "触发条件复审时点", "触发条件/复审时点", "触发", "复审时点", "阈值",
        "trigger", "triggers", "signal", "signals", "watchfor", "threshold",
    ],
    "evidence": [
        "依据", "证据", "evidence", "basis",
    ],
}

# Ordered keyword fallback for headers never seen before. First hit wins, so
# more specific fragments must come first.
_HEADER_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("trigger", ("触发", "复审", "阈值")),
    ("why_now", ("现在", "时机", "为何", "为什么")),
    ("acceptance", ("验收", "完成标准")),
    ("owner_role", ("负责", "角色", "承担")),
    ("deadline", ("截止", "时限", "期限", "何时")),
    ("topic", ("观察", "议题", "主题", "关注")),
    ("expected_benefit", ("收益", "价值", "好处")),
    ("success_criteria", ("成功标准", "判定", "通过")),
    ("risk", ("风险",)),
    ("priority", ("优先",)),
    ("effort", ("成本", "投入", "工作量")),
    ("method", ("方法", "步骤", "做法")),
    ("test", ("测试", "试验", "验证")),
    ("action", ("行动", "待办", "任务")),
]


def _normalize_header(text: str) -> str:
    """Normalize a Markdown table header for alias matching."""
    clean = re.sub(r"[*_`~\s]", "", (text or "").strip().lower())
    return re.sub(r"[/\\|:：\-]+", "", clean)


def _build_header_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for field, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            lookup.setdefault(_normalize_header(alias), field)
    return lookup


_HEADER_LOOKUP = _build_header_lookup()


def _canonical_header(header: str) -> Optional[str]:
    normalized = _normalize_header(header)
    if not normalized:
        return None
    field = _HEADER_LOOKUP.get(normalized)
    if field:
        return field
    for field, keywords in _HEADER_KEYWORDS:
        if any(kw in normalized for kw in keywords):
            return field
    return None


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


def _map_row(row: dict, warnings: list[str], context: str) -> dict:
    """Map a raw table row onto canonical field names.

    Unknown headers fall back to keyword matching; anything still unmapped is
    kept under ``col_<n>`` and reported so contract drift is visible.
    """
    mapped: dict[str, str] = {}
    for i, (key, value) in enumerate(row.items()):
        clean = (key or "").strip()
        if not clean:
            continue
        field = _canonical_header(clean)
        if field:
            mapped.setdefault(field, str(value or "").strip())
        else:
            label = _normalize_header(clean) or f"col_{i + 1}"
            mapped.setdefault(f"col_{i + 1}", str(value or "").strip())
            message = f"{context}: 未识别的表头 '{clean}'"
            if message not in warnings:
                warnings.append(message)
    return mapped


def canonicalize_rows(rows: list, primary: str, warnings: list[str],
                      context: str) -> list[dict]:
    """Validate and normalize item rows, keeping only usable ones.

    Rows without their primary text are dropped but always reported through
    ``warnings`` — a silent empty result previously hid 15 lost watchlist items.
    """
    clean_rows: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            warnings.append(f"{context}: 忽略非对象条目 {type(raw).__name__}")
            continue
        row = {k: str(v).strip() for k, v in raw.items() if v is not None}
        if not row.get(primary):
            alias = _canonical_header(primary)
            for key, value in row.items():
                if alias and _canonical_header(key) == alias and value:
                    row[primary] = value
                    break
        if not row.get(primary):
            fallback = next(
                (v for k, v in row.items()
                 if v and not k.startswith("col_") and _canonical_header(k) in
                 ("topic", "watch_point", "action", "test", "text")),
                "",
            ) or next((v for v in row.values() if v), "")
            if fallback:
                row[primary] = fallback
                warnings.append(
                    f"{context}: 缺少 '{primary}' 列，已用首列文本兜底: {fallback[:40]}"
                )
            else:
                warnings.append(f"{context}: 丢弃缺少 '{primary}' 的空条目")
                continue
        clean_rows.append(row)
    return clean_rows


def parse_action_items(p9_text: str) -> dict:
    """Extract immediate actions and this-week tests from a P9 report."""
    warnings: list[str] = []
    actions_section = _find_section(p9_text, "立即行动", "Immediate Actions")
    tests_section = _find_section(p9_text, "本周测试", "Test This Week")
    actions = canonicalize_rows(
        [_map_row(r, warnings, "actions") for r in _parse_table(actions_section)],
        "action", warnings, "actions")
    tests = canonicalize_rows(
        [_map_row(r, warnings, "tests") for r in _parse_table(tests_section)],
        "test", warnings, "tests")
    if p9_text and not actions and not tests:
        warnings.append("actions: P9 文档中未解析出任何行动或测试（检查章节标题与表格结构）")
    return {"actions": actions, "tests": tests, "warnings": warnings}


def parse_watchlist(p9_text: str) -> dict:
    """Extract watchlist rows from a P9 report."""
    warnings: list[str] = []
    section = _find_section(p9_text, "观察清单", "Watchlist")
    items = canonicalize_rows(
        [_map_row(r, warnings, "watchlist") for r in _parse_table(section)],
        "topic", warnings, "watchlist")
    if p9_text and not items:
        warnings.append("watchlist: P9 文档中未解析出任何观察项（检查章节标题与表格结构）")
    return {"items": items, "warnings": warnings}


def normalize_payload_actions(payload: Optional[dict]) -> tuple[list[dict], list[dict], list[str]]:
    """Canonicalize an actions payload loaded from JSON or parsed from P9."""
    payload = payload or {}
    warnings = [str(w) for w in (payload.get("warnings") or [])]
    actions = canonicalize_rows(
        [r if isinstance(r, dict) else {} for r in (payload.get("actions") or [])],
        "action", warnings, "actions(json)")
    tests = canonicalize_rows(
        [r if isinstance(r, dict) else {} for r in (payload.get("tests") or [])],
        "test", warnings, "tests(json)")
    return actions, tests, warnings


def normalize_payload_watchlist(payload: Optional[dict]) -> tuple[list[dict], list[str]]:
    payload = payload or {}
    warnings = [str(w) for w in (payload.get("warnings") or [])]
    items = canonicalize_rows(
        [r if isinstance(r, dict) else {} for r in (payload.get("items") or [])],
        "topic", warnings, "watchlist(json)")
    return items, warnings


def extract_sources(text: str) -> list[str]:
    """Return unique URLs found in Markdown text, in first-seen order."""
    seen: dict[str, None] = {}
    for match in _URL_RE.findall(text):
        url = match.rstrip(_TRAILING)
        if url and url not in seen:
            seen[url] = None
    return list(seen)


def _diff_key(text: str) -> str:
    text = re.sub(r"^\s*\d+\s*[.、)]\s*", "", text or "")
    return re.sub(r"\s+", " ", text).strip().lower()


def load_round_payloads(round_dir: str) -> Optional[dict]:
    """Load a round's action/watchlist/source payloads, from JSON or docs."""
    if not os.path.isdir(round_dir):
        return None

    documents = sorted(
        fn for fn in os.listdir(round_dir)
        if fn.endswith(".md") and os.path.isfile(os.path.join(round_dir, fn))
    )
    actions_path = os.path.join(round_dir, ACTION_FILE)
    watchlist_path = os.path.join(round_dir, WATCHLIST_FILE)
    sources_path = os.path.join(round_dir, SOURCES_FILE)

    def _read_json(path: str) -> Optional[dict]:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    p9_text = ""
    p9_name = next((fn for fn in documents if fn.startswith("09_")), None)
    if p9_name:
        with open(os.path.join(round_dir, p9_name), encoding="utf-8") as f:
            p9_text = f.read()

    actions = _read_json(actions_path)
    actions, tests, action_warnings = normalize_payload_actions(actions)
    if actions_path and not os.path.isfile(actions_path) and p9_text:
        parsed = parse_action_items(p9_text)
        actions, tests = parsed["actions"], parsed["tests"]
        action_warnings = parsed["warnings"]
    watchlist = _read_json(watchlist_path)
    watch_items, watch_warnings = normalize_payload_watchlist(watchlist)
    if watchlist_path and not os.path.isfile(watchlist_path) and p9_text:
        parsed_watch = parse_watchlist(p9_text)
        watch_items = parsed_watch["items"]
        watch_warnings = parsed_watch["warnings"]
    sources = _read_json(sources_path)
    if sources is None:
        urls: list[str] = []
        for fn in documents:
            with open(os.path.join(round_dir, fn), encoding="utf-8") as f:
                for url in extract_sources(f.read()):
                    if url not in urls:
                        urls.append(url)
        sources = {"sources": [{"url": u} for u in urls], "documents": documents}
    return {
        "actions": {"actions": actions, "tests": tests, "warnings": action_warnings},
        "watchlist": {"items": watch_items, "warnings": watch_warnings},
        "sources": sources,
    }


def _diff_rows(old_rows: list, new_rows: list, key: str) -> dict:
    old_map = {_diff_key(r.get(key, "")): r for r in old_rows if r.get(key)}
    new_map = {_diff_key(r.get(key, "")): r for r in new_rows if r.get(key)}
    added = [new_map[k] for k in new_map if k not in old_map]
    removed = [old_map[k] for k in old_map if k not in new_map]
    persisted = [new_map[k] for k in new_map if k in old_map]
    return {"added": added, "removed": removed, "persisted": persisted}


def _domain(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url or "").split("/", 1)[0]


def diff_rounds(round_a_dir: str, round_b_dir: str,
                date_a: Optional[str] = None,
                date_b: Optional[str] = None) -> Optional[dict]:
    """Structured content diff between two rounds.

    ``round_b`` is the newer round. Returns ``None`` when either directory
    is missing.
    """
    payload_a = load_round_payloads(round_a_dir)
    payload_b = load_round_payloads(round_b_dir)
    if payload_a is None or payload_b is None:
        return None

    date_a = date_a or os.path.basename(round_a_dir.rstrip(os.sep))
    date_b = date_b or os.path.basename(round_b_dir.rstrip(os.sep))

    actions = _diff_rows(payload_a["actions"].get("actions") or [],
                         payload_b["actions"].get("actions") or [], "action")
    tests = _diff_rows(payload_a["actions"].get("tests") or [],
                       payload_b["actions"].get("tests") or [], "test")
    watchlist = _diff_rows(payload_a["watchlist"].get("items") or [],
                           payload_b["watchlist"].get("items") or [], "topic")

    old_urls = {s.get("url") for s in payload_a["sources"].get("sources") or [] if s.get("url")}
    new_urls = {s.get("url") for s in payload_b["sources"].get("sources") or [] if s.get("url")}
    added_urls = sorted(new_urls - old_urls)
    removed_urls = sorted(old_urls - new_urls)
    old_domains = {_domain(u) for u in old_urls}
    new_domains = sorted({_domain(u) for u in added_urls} - old_domains)

    return {
        "from": date_a,
        "to": date_b,
        "actions": actions,
        "tests": tests,
        "watchlist": watchlist,
        "sources": {
            "added": added_urls,
            "removed": removed_urls,
            "new_domains": new_domains,
        },
        "counts": {
            "actions_added": len(actions["added"]),
            "actions_removed": len(actions["removed"]),
            "tests_added": len(tests["added"]),
            "watchlist_added": len(watchlist["added"]),
            "watchlist_removed": len(watchlist["removed"]),
            "sources_added": len(added_urls),
            "sources_removed": len(removed_urls),
        },
    }


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

    parsed = parse_action_items(p9_text) if p9_text else {
        "actions": [], "tests": [], "warnings": ["未找到 P9 综合文档，无法抽取行动项"],
    }
    actions_payload = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generated_at": generated_at,
        "source_document": p9_name,
        "actions": parsed["actions"],
        "tests": parsed["tests"],
        "warnings": parsed["warnings"],
    }
    watch_parsed = parse_watchlist(p9_text) if p9_text else {
        "items": [], "warnings": ["未找到 P9 综合文档，无法抽取观察项"],
    }
    watchlist_payload = {
        "schema_version": SCHEMA_VERSION,
        "date": date,
        "generated_at": generated_at,
        "source_document": p9_name,
        "items": watch_parsed["items"],
        "warnings": watch_parsed["warnings"],
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
    for warning in parsed["warnings"] + watch_parsed["warnings"]:
        logger.warning("Round %s artifact contract: %s", date, warning)
    logger.info(
        "Round %s artifacts written: %d actions, %d tests, %d watchlist items, %d sources",
        date, len(actions_payload["actions"]), len(actions_payload["tests"]),
        len(watchlist_payload["items"]), sources_payload["total_unique"],
    )
    return written
