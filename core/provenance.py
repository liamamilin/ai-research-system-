"""Citation provenance: did the report's URLs come from this run's searches?

A report can look well-sourced while citing links the model invented, or links
it truncated (``https://www.reuters.com/...``). This module normalizes URLs,
matches the report's citations against the URLs actually retrieved during the
run, and optionally probes the unmatched ones for reachability.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from typing import Iterable, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'，。；：、）】》]+", re.IGNORECASE)
TRAILING = ".,;:!?、，。；：）】》"

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "spm", "from", "share_source", "_hsenc", "_hsmi", "yclid", "msclkid",
}

DEFAULT_PORTS = {"http": "80", "https": "443"}


def extract_urls(text: str) -> list[str]:
    """Unique URLs from Markdown, in first-seen order, trimmed of punctuation."""
    seen: dict[str, None] = {}
    for raw in URL_RE.findall(text or ""):
        url = raw.rstrip(TRAILING)
        if url and url not in seen:
            seen[url] = None
    return list(seen)


def normalize_url(url: str) -> str:
    """Canonical form for comparison: host/scheme lowercased, tracking stripped."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    if not parts.scheme or not parts.netloc:
        return url.strip().lower()

    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    netloc = host
    if parts.port and str(parts.port) != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{parts.port}"

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ])
    return urlunsplit((scheme, netloc, path, query, ""))


def check(text: str, retrieved: Iterable[str], verify_unreachable: bool = False,
          max_checks: int = 10, timeout: float = 5.0) -> dict:
    """Compare report citations against the run's retrieved URLs.

    Returns a summary dict with totals, unmatched URLs and (optionally)
    reachability for the unmatched ones — those are the likely hallucinations.
    """
    cited = extract_urls(text)
    retrieved_norm = {normalize_url(u) for u in (retrieved or []) if u}

    matched: list[str] = []
    unmatched: list[str] = []
    for url in cited:
        (matched if normalize_url(url) in retrieved_norm else unmatched).append(url)

    result = {
        "total": len(cited),
        "matched": len(matched),
        "unmatched": len(unmatched),
        "coverage": round(len(matched) / len(cited), 4) if cited else None,
        "unmatched_examples": unmatched[:10],
        "retrieved_count": len(retrieved_norm),
        "checked_at": None,
        "reachability": None,
    }
    if not cited:
        return result

    if verify_unreachable and unmatched:
        result["reachability"] = probe(unmatched[:max_checks], timeout=timeout)
    return result


def probe(urls: list[str], timeout: float = 5.0) -> dict:
    """HEAD (then GET fallback) each URL. Never raises."""
    outcomes: dict[str, str] = {}
    for url in urls:
        outcomes[url] = _probe_one(url, timeout)
    reachable = sum(1 for v in outcomes.values() if v == "ok")
    return {
        "checked": len(outcomes),
        "reachable": reachable,
        "results": outcomes,
    }


def _probe_one(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={
            "User-Agent": "AI-Research-Console/1.0 (+citation-check)",
            "Accept": "*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = getattr(response, "status", 0) or response.getcode()
            return "ok" if 200 <= int(code) < 400 else f"http_{code}"
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 405, 501):
            return _probe_get(url, timeout)
        return f"http_{exc.code}"
    except urllib.error.URLError:
        return _probe_get(url, timeout)
    except (TimeoutError, ValueError, OSError) as exc:
        return f"error:{type(exc).__name__}"


def _probe_get(url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "AI-Research-Console/1.0 (+citation-check)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            code = getattr(response, "status", 0) or response.getcode()
            return "ok" if 200 <= int(code) < 400 else f"http_{code}"
    except urllib.error.HTTPError as exc:
        return f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"error:{type(exc).__name__}"


def summarize(result: Optional[dict]) -> str:
    """One-line human summary for logs and the UI."""
    if not result:
        return "citation check unavailable"
    if not result.get("total"):
        return "报告未包含任何 URL"
    coverage = result.get("coverage")
    pct = f"{coverage * 100:.0f}%" if coverage is not None else "n/a"
    return (
        f"引用溯源：{result['matched']}/{result['total']} 可追溯（{pct}），"
        f"{result['unmatched']} 个未在本次检索结果中"
    )
