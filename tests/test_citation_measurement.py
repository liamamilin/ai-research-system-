"""The citation number must not be able to lie again.

The check reports one figure -- "82 of 103 citations traceable" -- and that
figure decides whether a report is trustworthy. It is computed by matching
strings, which means any drift in the extractor silently changes the number
without breaking anything.

Three separate times the number was wrong while the code passed every test:

* the two URL patterns had drifted, so a citation written the ordinary Chinese
  way (https://host/path（2026-09-22 发布）) was extracted with its own
  annotation glued on. 24 of P6's 54 citations were phantoms and the stage
  measured 56%.
* only each search result's own link was recorded, not the links in its
  snippet, so a link the model demonstrably read scored as fabricated.
* URLs handed to a synthesis stage in the injected upstream documents were
  never recorded at all.

So these tests use the shapes that actually appear in the corpus, and assert on
matching behaviour rather than on the regex.
"""

from __future__ import annotations

import pytest

from core import provenance
from core.provenance import check, extract_urls, normalize_url


# --- the shapes the reports actually use ------------------------------------

REAL_CITATIONS = [
    # Ordinary Chinese annotation after a link. This one cost 24 citations.
    "见 https://github.com/astral-sh/uv/security/advisories/GHSA-2cv4（公告发布 2026-09-24）。",
    # A link inside a search snippet, which the agent also has to see.
    "详见 https://www.infoworld.com/article/4221163/x.html",
    # Tracking parameters must not make it a different URL.
    "参考 https://example.com/a?utm_source=x&id=7",
    # Same article, mobile subdomain and trailing slash.
    "https://m.example.com/post/ 与 https://example.com/post",
    # Wrapped in full-width punctuation mid-sentence.
    "据 https://a.test/b。另见 https://c.test/d。",
    # Inside a Markdown link and in a table cell.
    "[标题](https://e.test/f) | https://g.test/h |",
    # Unbalanced trailing paren, which is how these get typed.
    "https://i.test/j（2026-09-22",
]


def test_a_chinese_annotation_is_not_part_of_the_link():
    urls = extract_urls(REAL_CITATIONS[0])
    assert urls == ["https://github.com/astral-sh/uv/security/advisories/GHSA-2cv4"]


def test_every_real_shape_yields_a_clean_url():
    for citation in REAL_CITATIONS:
        for url in extract_urls(citation):
            assert "（" not in url and "）" not in url, citation
            assert "。" not in url and "，" not in url, citation
            assert url.startswith(("http://", "https://")), url
            assert url == url.strip(), url


def test_mobile_and_desktop_subdomains_are_not_conflated():
    """Deliberate: `m.` is stripped, `www.` is not treated the same way.

    Stripping an `m.` prefix would let a report cite the mobile URL of a page
    the run retrieved on desktop. Measured against the 2026-09-28 round first:
    zero of the 359 sources use a mobile subdomain, so the gap costs nothing,
    and `m.` hosts are not reliably the same site (`m.` is a real subdomain on
    plenty of hosts where the content differs). Weakening a matcher whose whole
    job is catching fabrications, on no evidence, is the wrong trade.

    If a corpus ever does cite `m.` forms, this is the assertion to revisit --
    and the evidence to bring with it.
    """
    cited = extract_urls("https://m.example.com/post/ 与 https://example.com/post")
    assert len(cited) == 2, "both spellings are legitimate links"
    result = check("见 https://example.com/post", ["https://m.example.com/post/"])
    assert result["matched"] == 0, \
        "m. must not silently match the bare host"
    # www. *is* folded, which is the common and safe case.
    assert check("见 https://example.com/p", ["https://www.example.com/p/"])["matched"] == 1


def test_tracking_parameters_do_not_break_traceability():
    result = check(
        "参考 https://example.com/a?id=7&utm_source=news",
        ["https://example.com/a?id=7"],
    )
    assert result["matched"] == 1, result


def test_a_url_read_in_a_snippet_is_traceable():
    """The retrieved pool is what the model was shown, not just result links."""
    snippet_block = (
        "(10 sources retrieved from web search)\n\n---\n\n"
        "### [Aggregator roundup](https://news.example/roundup)\n\n"
        "详见 https://www.infoworld.com/article/4221163/x.html\n\n---\n\n"
    )
    remembered = set(extract_urls(snippet_block))
    assert "https://news.example/roundup" in remembered
    assert "https://www.infoworld.com/article/4221163/x.html" in remembered

    report = "依据 https://www.infoworld.com/article/4221163/x.html 的结论"
    assert check(report, remembered)["matched"] == 1


def test_a_url_that_appears_from_nowhere_is_still_caught():
    """The check must not become so lenient that it stops detecting anything."""
    result = check(
        "结论见 https://invented.example/never-retrieved",
        ["https://real.example/a"],
    )
    assert result["unmatched"] == 1
    assert result["coverage"] == 0.0
    assert result["unmatched_examples"] == ["https://invented.example/never-retrieved"]


def test_coverage_is_a_ratio_of_real_citations():
    result = check(
        "a https://real.example/1 b https://real.example/2 c https://fake.example/3",
        ["https://real.example/1", "https://real.example/2"],
    )
    assert (result["total"], result["matched"], result["unmatched"]) == (3, 2, 1)
    assert result["coverage"] == pytest.approx(2 / 3, abs=1e-3)


def test_no_urls_is_not_zero_coverage():
    """A report with no links has nothing to trace, not a failing grade."""
    result = check("这份报告没有任何外链。", ["https://real.example/1"])
    assert result["total"] == 0
    assert result["coverage"] is None
    assert "未包含任何 URL" in provenance.summarize(result)


# --- one pattern, so the two sides cannot spell a URL differently ------------

def test_the_agent_and_the_check_extract_identically():
    """A URL the agent remembered and the same URL the report cites must match.

    Two copies of this pattern had drifted, each missing a bracket the other
    had, which is how a legitimately-read citation scored as fabricated.
    """
    from core.research import _URL_RE

    assert _URL_RE is provenance.URL_RE
    for citation in REAL_CITATIONS:
        # extract_urls trims trailing punctuation after matching; the raw
        # pattern must agree on where each URL ends.
        assert set(_URL_RE.findall(citation)) == set(
            provenance.URL_RE.findall(citation))
