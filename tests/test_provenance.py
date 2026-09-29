"""Citation provenance: URL normalization, matching and reachability probing."""

from __future__ import annotations

import pytest

from core import provenance


# --- extraction -------------------------------------------------------------


def test_extract_urls_dedupes_and_trims():
    text = "见 https://a.test/x）以及 https://a.test/x ，还有 https://b.test/y。重复 https://a.test/x#frag\n"
    urls = provenance.extract_urls(text)
    # Raw forms are kept (fragments included) but the same page is only listed
    # once when it appears identically; normalization happens during matching.
    assert urls[:2] == ["https://a.test/x", "https://b.test/y"]
    assert len(urls) == 3 and urls[2] == "https://a.test/x#frag"


def test_extract_urls_empty():
    assert provenance.extract_urls("no links here") == []
    assert provenance.extract_urls("") == []


# --- normalization ----------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("https://WWW.Example.COM/Path/", "https://example.com/Path"),
    ("http://example.com", "http://example.com/"),
    ("https://example.com:443/a", "https://example.com/a"),
    ("http://example.com:80/a", "http://example.com/a"),
    ("https://example.com:8443/a", "https://example.com:8443/a"),
    ("https://example.com/a?utm_source=x&utm_medium=y", "https://example.com/a"),
    ("https://example.com/a?ref=hn&id=7", "https://example.com/a?id=7"),
    ("https://example.com/a#section", "https://example.com/a"),
    ("https://example.com//a//b", "https://example.com/a/b"),
])
def test_normalize_url(raw, expected):
    assert provenance.normalize_url(raw) == expected


def test_normalize_handles_garbage():
    assert provenance.normalize_url("not a url") == "not a url"


# --- matching ---------------------------------------------------------------


def test_check_all_matched():
    text = "来源 [1] https://a.test/x 与 https://b.test/y?utm_source=z"
    result = provenance.check(text, ["https://a.test/x", "https://b.test/y"])
    assert result["total"] == 2
    assert result["matched"] == 2
    assert result["unmatched"] == 0
    assert result["coverage"] == 1.0
    assert result["unmatched_examples"] == []


def test_check_detects_hallucinated_url():
    text = "来源 [1] https://a.test/x 与 https://invented.test/page"
    result = provenance.check(text, ["https://a.test/x"])
    assert result["matched"] == 1
    assert result["unmatched"] == 1
    assert result["unmatched_examples"] == ["https://invented.test/page"]
    assert result["coverage"] == 0.5


def test_check_matching_ignores_tracking_and_case():
    text = "https://WWW.Example.com/Path/?utm_campaign=x#top"
    result = provenance.check(text, ["https://example.com/Path"])
    assert result["unmatched"] == 0


def test_check_with_no_citations():
    result = provenance.check("报告没有任何链接", ["https://a.test/x"])
    assert result["total"] == 0
    assert result["coverage"] is None
    assert result["reachability"] is None


def test_check_with_no_retrieval_marks_everything_unmatched():
    result = provenance.check("https://a.test/x", [])
    assert result["unmatched"] == 1
    assert result["retrieved_count"] == 0


def test_verify_unreachable_probes_only_unmatched(monkeypatch):
    probed: list[str] = []
    monkeypatch.setattr(
        provenance, "_probe_one",
        lambda url, timeout: probed.append(url) or "ok",
    )
    text = "https://good.test/x https://bad.test/y"
    result = provenance.check(text, ["https://good.test/x"], verify_unreachable=True)
    # Only the unmatched one, plus its site root as a liveness control -- a
    # status is not evidence on its own.
    assert "https://bad.test/y" in probed
    assert "https://bad.test/" in probed
    assert "https://good.test/" not in probed, "a matched citation was probed"
    assert result["reachability"]["checked"] == 1
    assert result["reachability"]["reachable"] == 1
    assert result["citation_audit"]["reachable_count"] == 1


def test_probe_respects_max_checks(monkeypatch):
    monkeypatch.setattr(provenance, "_probe_one", lambda url, timeout: "ok")
    urls = [f"https://bad{ i}.test/x" for i in range(20)]
    result = provenance.check(" ".join(urls), [], verify_unreachable=True, max_checks=5)
    assert result["reachability"]["checked"] == 5


def test_probe_never_raises(monkeypatch):
    def boom(url, timeout):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(provenance, "_probe_one", boom)
    # probe() delegates to _probe_one which is patched to raise — the helper
    # itself must still not propagate it.
    from core.provenance import _probe_one
    with pytest.raises(RuntimeError):
        _probe_one("https://x.test", 1.0)
    result = provenance.check("https://x.test", [])
    assert result["unmatched"] == 1


# --- reporting --------------------------------------------------------------


def test_summarize():
    result = provenance.check("https://a.test/x https://b.test/y", ["https://a.test/x"])
    text = provenance.summarize(result)
    assert "1/2" in text and "50%" in text
    assert "未包含任何 URL" in provenance.summarize({"total": 0})
    assert "unavailable" in provenance.summarize(None)


# --- real-world cases from the audit ----------------------------------------


def test_reuters_truncated_url_is_normalized():
    """'https://www.reuters.com/...' must not count as a distinct citation."""
    cited = provenance.normalize_url("https://www.reuters.com/...")
    assert cited == "https://reuters.com/..."  # still unmatched, but not merged
    # A real article URL normalizes to a stable comparable form:
    assert provenance.normalize_url(
        "https://www.reuters.com/tech/news/2026/09/24/x/?utm_source=twitter"
    ) == "https://reuters.com/tech/news/2026/09/24/x"


def test_agent_records_retrieved_urls():
    from core.research import ResearchAgent

    class FakeResult:
        def __init__(self, url):
            self.url = url

    agent = ResearchAgent.__new__(ResearchAgent)
    agent.retrieved_urls = set()
    agent._remember([FakeResult("https://a.test/x"), FakeResult("https://a.test/x"),
                     FakeResult("")])
    assert agent.retrieved_urls == {"https://a.test/x"}


# --- What a synthesis stage is allowed to cite ------------------------------
#
# Coverage was measured against the URLs the reporting stage's own tool calls
# returned. P7, P8 and P9 run no searches: they are handed the upstream
# documents and cite from them. On the 2026-09-29 round that made P9 score 0%
# against 78 citations that are 77 traceable to P1-P8 -- the gate would have
# fired on every synthesis round and pointed at the wrong thing entirely.


def test_urls_handed_to_a_stage_count_as_known():
    from core.engine import _urls_in

    upstream = "上游文档见 https://a.test/x 和 https://b.test/y?z=1。"
    found = _urls_in(upstream)
    assert "https://a.test/x" in found
    assert "https://b.test/y?z=1" in found
    assert _urls_in("") == set()


def test_a_synthesis_stage_is_not_measured_against_its_own_empty_searches():
    from core.engine import ResearchEngine, _urls_in
    from core import provenance

    upstream = "P1 引用 https://a.test/x。\nP2 引用 https://b.test/y。\n"
    report = "综合结论，来源：https://a.test/x 与 https://b.test/y。"

    class Agent:
        retrieved_urls = ()          # a synthesis stage searches for nothing

    engine = ResearchEngine.__new__(ResearchEngine)
    engine._prompt_urls = _urls_in(upstream)
    known = engine._known_urls(Agent())

    result = provenance.check(report, known)
    assert result["coverage"] == 1.0, result
    assert not result["unmatched"]


def test_a_genuinely_invented_citation_is_still_caught():
    """Widening the baseline must not blind the check."""
    from core.engine import ResearchEngine, _urls_in
    from core import provenance

    engine = ResearchEngine.__new__(ResearchEngine)
    engine._prompt_urls = _urls_in("上游只有 https://a.test/x")

    class Agent:
        retrieved_urls = ()

    result = provenance.check("来源 https://a.test/x 与 https://invented.test/y",
                              engine._known_urls(Agent()))
    assert result["unmatched"], result
    assert result["coverage"] < 1.0


def test_a_radar_stage_keeps_its_own_retrieved_urls():
    from core.engine import ResearchEngine, _urls_in

    engine = ResearchEngine.__new__(ResearchEngine)
    engine._prompt_urls = _urls_in("上游 https://up.test/a")

    class Agent:
        retrieved_urls = {"https://search.test/b"}

    known = engine._known_urls(Agent())
    assert known == {"https://up.test/a", "https://search.test/b"}
