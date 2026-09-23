"""Subscription source adapters (RSS/Atom, GitHub Releases, arXiv, HN).

Each adapter returns :class:`core.search.SearchResult` items so they merge
with web-search results. Specs are strings:

* ``rss:https://example.com/feed.xml``  (also ``feed:``)
* ``github:owner/repo``
* ``arxiv:query``
* ``hn:query``
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from .search import SearchClient, SearchResult

logger = logging.getLogger(__name__)

_UA = "BaseCodingCLi-ResearchAgent/2.0"
_ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
_TAG_RE = re.compile(r"<[^>]+>")


def _get_text(url: str, timeout: int, accept: str = "*/*") -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _get_json(url: str, timeout: int) -> dict:
    return json.loads(_get_text(url, timeout, accept="application/json"))


def _clean(text: Optional[str], limit: int = 400) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _parse_feed(xml_text: str, limit: int) -> list[SearchResult]:
    """Parse RSS 2.0 or Atom into SearchResult items."""
    root = ET.fromstring(xml_text)
    results: list[SearchResult] = []

    for item in root.findall(".//item")[:limit]:
        results.append(SearchResult(
            title=_clean(item.findtext("title"), 200),
            url=(item.findtext("link") or "").strip(),
            excerpts=[_clean(item.findtext("description"))] if item.findtext("description") else [],
            publish_date=(item.findtext("pubDate") or "").strip(),
            provider="rss",
        ))
    if results:
        return results

    for entry in root.findall(".//a:entry", _ATOM_NS)[:limit]:
        link = ""
        for node in entry.findall("a:link", _ATOM_NS):
            if node.get("rel", "alternate") == "alternate" and node.get("href"):
                link = node.get("href")
                break
        summary = entry.findtext("a:summary", default="", namespaces=_ATOM_NS)
        results.append(SearchResult(
            title=_clean(entry.findtext("a:title", default="", namespaces=_ATOM_NS), 200),
            url=link.strip(),
            excerpts=[_clean(summary)] if summary else [],
            publish_date=(entry.findtext("a:updated", default="", namespaces=_ATOM_NS)
                          or entry.findtext("a:published", default="", namespaces=_ATOM_NS)).strip(),
            provider="rss",
        ))
    return results


def fetch_rss(url: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    return _parse_feed(_get_text(url, timeout, accept="application/rss+xml, application/xml"), limit)


def fetch_github_releases(repo: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    token = os.environ.get("GITHUB_TOKEN", "")
    headers = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"https://api.github.com/repos/{repo}/releases?per_page={max(1, min(limit, 30))}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        releases = json.loads(resp.read().decode("utf-8", "replace"))

    results: list[SearchResult] = []
    for rel in releases[:limit]:
        name = rel.get("name") or rel.get("tag_name") or ""
        results.append(SearchResult(
            title=_clean(f"{repo} {name}".strip(), 200),
            url=rel.get("html_url") or f"https://github.com/{repo}/releases",
            excerpts=[_clean(rel.get("body"))] if rel.get("body") else [],
            publish_date=(rel.get("published_at") or "")[:10],
            provider="github",
        ))
    return results


def fetch_arxiv(query: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": max(1, min(limit, 30)),
    })
    xml_text = _get_text(f"http://export.arxiv.org/api/query?{params}", timeout)
    results = _parse_feed(xml_text, limit)
    for item in results:
        item.provider = "arxiv"
    return results


def fetch_hn(query: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    params = urllib.parse.urlencode({
        "query": query,
        "tags": "story",
        "hitsPerPage": max(1, min(limit, 30)),
    })
    data = _get_json(f"https://hn.algolia.com/api/v1/search_by_date?{params}", timeout)
    results: list[SearchResult] = []
    for hit in (data.get("hits") or [])[:limit]:
        results.append(SearchResult(
            title=_clean(hit.get("title") or hit.get("story_title"), 200),
            url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            excerpts=[_clean(hit.get("story_text"))] if hit.get("story_text") else [],
            publish_date=(hit.get("created_at") or "")[:10],
            provider="hn",
        ))
    return results


_ADAPTERS = {
    "rss": fetch_rss,
    "feed": fetch_rss,
    "github": fetch_github_releases,
    "arxiv": fetch_arxiv,
    "hn": fetch_hn,
}


def fetch_source(spec: str, limit: int = 8, timeout: int = 20) -> list[SearchResult]:
    """Fetch one source spec (``kind:target``)."""
    kind, _, target = (spec or "").partition(":")
    kind = kind.strip().lower()
    target = target.strip()
    adapter = _ADAPTERS.get(kind)
    if not adapter or not target:
        logger.warning("Unknown source spec: %s", spec)
        return []
    return adapter(target, limit=limit, timeout=timeout)


def fetch_all(sources: list[str], limit_per_source: int = 8,
              timeout: int = 20) -> list[SearchResult]:
    """Fetch every source spec, dedupe by URL, keep first-seen order."""
    collected: list[SearchResult] = []
    for spec in sources or []:
        try:
            collected.extend(fetch_source(spec, limit=limit_per_source, timeout=timeout))
        except Exception as exc:  # noqa: BLE001 - one bad source must not break the run
            logger.warning("Source %s failed: %s", spec, str(exc)[:200])
    return SearchClient._dedupe(collected)
