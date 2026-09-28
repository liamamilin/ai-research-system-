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
from concurrent.futures import ThreadPoolExecutor
from typing import Iterable, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

URL_RE = re.compile(
    r"https?://[^\s<>()\[\]{}\"'`\\^|，。、；：？！…—～"
    r"（）「」『』【】《》〈〉“”‘’　]+",
    re.IGNORECASE,
)
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
        # Populated only when verify_unreachable is on.
        "citation_audit": None,
    }
    if not cited:
        return result

    if verify_unreachable and unmatched:
        result["reachability"] = probe_with_controls(
            unmatched[:max_checks], timeout=timeout)
        # Store the verdict, not just the raw statuses, so every consumer
        # answers the same question the same way.
        result["citation_audit"] = {
            k: v for k, v in result["reachability"].items()
            if k not in ("results", "controls")
        }
    return result


#: A status that means "not there" -- but only in comparison with a control.
_ABSENT = ("http_404", "http_410")
_INCONCLUSIVE_PREFIXES = ("http_", "error:")

#: The control is the site root, not a random nonexistent path. Almost every
#: host answers 404 to a path that does not exist, so "real 404, control 404"
#: is the *normal* case and distinguishes nothing. The root is a path that
#: should work if the site is up at all, so a root that 404s or 403s means the
#: host is unhealthy or refusing automated clients -- and then its 404 on the
#: citation is worth nothing.
_CONTROL_PATH = "/"


def control_url_for(url: str) -> Optional[str]:
    """The site root for ``url``'s origin, used as a liveness control."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def audit_urls(pairs: dict[str, str], controls: dict[str, str],
               limit: int = 10) -> dict:
    """Classify probed citations against a per-host control.

    A status on its own is not evidence. Two hosts in the 2026-09-28 round
    answered 403 and 404 respectively to *every* request -- the site root, a
    control path, the article -- because they refuse automated clients. Reading
    those as "fabricated" would have named two real sources as inventions, and
    the difference between a wrong accusation and a right one is one extra
    request per host.

    So a 404 only counts when the same host answers the control differently.
    """

    def absent(status: str) -> bool:
        return status in _ABSENT

    verdicts: dict[str, str] = {}
    for url, status in pairs.items():
        control = controls.get(url)
        if status == "ok":
            verdicts[url] = "reachable"
        elif absent(status):
            verdicts[url] = ("absent" if control is not None and not absent(control)
                             else "undecidable")
        elif status.startswith(_INCONCLUSIVE_PREFIXES):
            verdicts[url] = "undecidable"
        else:
            verdicts[url] = "undecidable"

    def bucket(name):
        return [u for u, v in verdicts.items() if v == name]

    live = bucket("reachable")
    gone = bucket("absent")
    maybe = bucket("undecidable")
    return {
        "probed": len(pairs),
        "reachable_count": len(live),
        "absent": gone[:limit],
        "absent_count": len(gone),
        "undecidable": [
            {"url": u, "status": pairs[u]} for u in maybe[:limit]
        ],
        "undecidable_count": len(maybe),
    }


def audit_reachability(reachability: Optional[dict], limit: int = 10) -> dict:
    """Classify without controls, for the single-URL case.

    Used when a check runs on a single citation, where a second request to the
    same host would usually be refused for the same reason. Anything that is
    not a clean 404 against a known-good host is reported as undecidable rather
    than guessed at.
    """
    results = (reachability or {}).get("results") or {}
    absent, refused, live = [], [], []
    for url, status in results.items():
        if status == "ok":
            live.append(url)
        elif status in _ABSENT:
            absent.append(url)
        elif status.startswith(_INCONCLUSIVE_PREFIXES):
            refused.append((url, status))
    return {
        "probed": len(results),
        "absent": absent[:limit],
        "absent_count": len(absent),
        "undecidable": [{"url": u, "status": s} for u, s in refused][:limit],
        "undecidable_count": len(refused),
        "reachable_count": len(live),
        "controlled": False,
    }


def probe(urls: list[str], timeout: float = 5.0, workers: int = 8) -> dict:
    """HEAD (then GET fallback) each URL. Never raises.

    Concurrent, because it runs inside a report run: sequentially, ten URLs at a
    five-second timeout is up to fifty seconds of a finished report waiting on
    classification nobody asked for. Bounded, and the pool is torn down before
    returning, so a slow host cannot outlive the call.
    """
    if not urls:
        return {"checked": 0, "reachable": 0, "results": {}}
    if len(urls) == 1:
        outcomes = {urls[0]: _probe_one(urls[0], timeout)}
    else:
        outcomes = {}
        with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
            futures = {pool.submit(_probe_one, url, timeout): url for url in urls}
            for future, url in futures.items():
                try:
                    outcomes[url] = future.result()
                except Exception as exc:  # noqa: BLE001 - a probe never breaks a run
                    outcomes[url] = f"error:{type(exc).__name__}"
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


def probe_with_controls(urls: list[str], timeout: float = 5.0,
                        workers: int = 8) -> dict:
    """Probe each URL *and* a control path on the same host, then audit.

    This is the only version whose "fabricated" verdict means anything, and the
    reason is not subtle: in the 2026-09-28 round, two of the citations that a
    naive check called fabricated were hosts answering 403 and 404 to every
    request including their own homepage. One request per host buys the
    difference between an accusation and an observation.
    """
    if not urls:
        return {"probed": 0, "reachable_count": 0, "absent": [],
                "absent_count": 0, "undecidable": [], "undecidable_count": 0,
                "controlled": True, "results": {}, "controls": {}}

    controls = {url: control_url_for(url) for url in urls}
    todo = list(urls) + [c for c in controls.values() if c]
    outcomes = probe(todo, timeout=timeout, workers=workers)["results"]

    pairs = {url: outcomes.get(url, "error:Unknown") for url in urls}
    ctrl = {}
    for url, control in controls.items():
        if control:
            ctrl[url] = outcomes.get(control, "error:Unknown")
    audit = audit_urls(pairs, ctrl)
    # `checked` and `reachable` are kept from the original probe() shape:
    # this dict is written into report metadata and read by the reports API,
    # so dropping them would break every report that already has one.
    audit["checked"] = len(urls)
    audit["reachable"] = audit["reachable_count"]
    audit["results"] = pairs
    audit["controls"] = ctrl
    return audit


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
