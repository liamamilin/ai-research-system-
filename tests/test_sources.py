"""Tests for subscription source adapters."""

from __future__ import annotations

import json

from core import sources

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Release 1.2</title>
    <link>https://example.com/rel-1-2</link>
    <description>&lt;p&gt;Big &lt;b&gt;update&lt;/b&gt; here&lt;/p&gt;</description>
    <pubDate>Mon, 01 Sep 2026 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Release 1.1</title>
    <link>https://example.com/rel-1-1</link>
    <description>Older</description>
  </item>
</channel></rss>
"""

ATOM_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Paper A</title>
    <link rel="alternate" href="https://arxiv.org/abs/2609.00001"/>
    <summary>Abstract text</summary>
    <updated>2026-09-20T00:00:00Z</updated>
  </entry>
</feed>
"""


def test_fetch_rss_parses_items(monkeypatch):
    monkeypatch.setattr(sources, "_get_text", lambda url, timeout, accept="*/*": RSS_XML)
    results = sources.fetch_rss("https://example.com/feed.xml", limit=5)
    assert len(results) == 2
    assert results[0].title == "Release 1.2"
    assert results[0].url == "https://example.com/rel-1-2"
    assert results[0].excerpts == ["Big update here"]
    assert results[0].provider == "rss"


def test_fetch_arxiv_parses_atom(monkeypatch):
    monkeypatch.setattr(sources, "_get_text", lambda url, timeout, accept="*/*": ATOM_XML)
    results = sources.fetch_arxiv("agent memory", limit=3)
    assert results[0].title == "Paper A"
    assert results[0].url == "https://arxiv.org/abs/2609.00001"
    assert results[0].provider == "arxiv"
    assert results[0].publish_date.startswith("2026-09-20")


def test_fetch_github_releases(monkeypatch):
    payload = json.dumps([
        {"tag_name": "v2.0", "name": "v2.0 Big", "html_url": "https://github.com/o/r/releases/tag/v2.0",
         "published_at": "2026-09-18T10:00:00Z", "body": "notes"},
    ]).encode()

    class Resp:
        def read(self):
            return payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sources.urllib.request, "urlopen", lambda req, timeout=None: Resp())
    results = sources.fetch_github_releases("o/r", limit=3)
    assert results[0].title == "o/r v2.0 Big"
    assert results[0].publish_date == "2026-09-18"
    assert results[0].provider == "github"


def test_fetch_hn(monkeypatch):
    data = {"hits": [
        {"title": "Show HN: X", "url": "https://x.test", "created_at": "2026-09-19T00:00:00Z",
         "objectID": "1"},
        {"title": "Ask HN: Y", "story_text": "text", "created_at": "2026-09-18T00:00:00Z",
         "objectID": "2"},
    ]}
    monkeypatch.setattr(sources, "_get_json", lambda url, timeout: data)
    results = sources.fetch_hn("agent", limit=5)
    assert results[0].url == "https://x.test"
    assert results[1].url == "https://news.ycombinator.com/item?id=2"
    assert results[1].provider == "hn"


def test_fetch_source_dispatch_and_unknown():
    assert sources.fetch_source("nope:whatever") == []
    assert sources.fetch_source("rss:") == []


def test_engine_injects_sources_block(tmp_path, monkeypatch):
    from core.engine import ResearchEngine

    engine = ResearchEngine(config_dir=str(tmp_path / "config"),
                            jobs_dir=str(tmp_path / "jobs"))
    monkeypatch.setattr(sources, "fetch_all", lambda specs, limit_per_source=8: [
        sources.SearchResult(title="Release", url="https://x.test/1",
                             excerpts=["body"])])

    block = engine._fetch_sources_block({"sources": ["github:o/r"]})
    assert "Pre-fetched subscription sources" in block
    assert "https://x.test/1" in block
    assert engine._fetch_sources_block({}) == ""


def test_engine_sources_failure_is_silent(tmp_path, monkeypatch):
    from core.engine import ResearchEngine

    engine = ResearchEngine(config_dir=str(tmp_path / "config"),
                            jobs_dir=str(tmp_path / "jobs"))

    def boom(specs, limit_per_source=8):
        raise OSError("network down")

    monkeypatch.setattr(sources, "fetch_all", boom)
    assert engine._fetch_sources_block({"sources": ["rss:https://x/feed"]}) == ""


def test_fetch_all_dedupes_and_survives_failures(monkeypatch):
    def fake_fetch(spec, limit=8, timeout=20):
        if "bad" in spec:
            raise OSError("boom")
        return [
            sources.SearchResult(title="A", url="https://dup.test/1", excerpts=["x"]),
            sources.SearchResult(title="B", url="https://b.test/2", excerpts=["y"]),
        ]

    monkeypatch.setattr(sources, "fetch_source", fake_fetch)
    results = sources.fetch_all(["rss:https://bad.test/f", "hn:q"])
    assert [r.title for r in results] == ["A", "B"]
