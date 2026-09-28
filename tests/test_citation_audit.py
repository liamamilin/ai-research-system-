"""Naming a citation "fabricated" is an accusation, so the evidence has to hold.

Three separate times in one afternoon, a URL that looked invented turned out
not to be, and each time the reason was the same: the host refused the probe.
``securityboulevard.com`` answers 403 to everything. ``thenextweb.com`` answers
404 to everything, its own homepage included. Reading those as "does not exist"
would have named two real articles as fabrications.

So a status is never evidence on its own. Each URL is probed alongside its
site's root, and only a 404 from a host whose root works counts.
"""

from __future__ import annotations

from core import provenance
from core.provenance import audit_urls, control_url_for, probe_with_controls


# --- the control is the site root -------------------------------------------

def test_the_control_is_the_origin_root():
    assert control_url_for("https://a.test/deep/path?q=1") == "https://a.test/"
    assert control_url_for("http://a.test:8080/x") == "http://a.test:8080/"


def test_a_malformed_url_has_no_control():
    assert control_url_for("not a url") is None
    assert control_url_for("") is None


# --- why the root, and not a random nonexistent path ------------------------

def test_a_nonexistent_path_would_be_useless_as_a_control():
    """Nearly every host 404s a path that does not exist.

    So "the real path 404s and so does a made-up path" is the ordinary case and
    distinguishes nothing. A host that soft-404s is indistinguishable from a
    host where the page is genuinely gone. The root is the only path that
    should work if the site is up.
    """
    pairs = {"https://a.test/real": "http_404"}
    controls = {"https://a.test/real": "http_404"}       # control = root, 404
    assert audit_urls(pairs, controls)["absent_count"] == 0

    # With a working root, the 404 becomes meaningful.
    assert audit_urls(pairs, {"https://a.test/real": "ok"})["absent_count"] == 1


# --- the three verdicts ------------------------------------------------------

def test_a_working_url_is_reachable():
    audit = audit_urls({"https://a.test/p": "ok"}, {"https://a.test/p": "ok"})
    assert audit["reachable_count"] == 1
    assert audit["absent_count"] == 0


def test_a_404_from_a_healthy_host_is_absent():
    audit = audit_urls(
        {"https://github.com/x/CHANGELOG.md": "http_404"},
        {"https://github.com/x/CHANGELOG.md": "ok"},   # github.com/ is 200
    )
    assert audit["absent"] == ["https://github.com/x/CHANGELOG.md"]


def test_a_403_is_never_evidence():
    """securityboulevard.com answers 403 to the article and to its own root."""
    audit = audit_urls(
        {"https://blocked.test/article": "http_403"},
        {"https://blocked.test/article": "http_403"},
    )
    assert audit["absent_count"] == 0
    assert audit["undecidable_count"] == 1
    assert "不代表编造" not in audit["undecidable"][0]["status"]


def test_a_404_from_a_host_that_404s_its_own_root_is_undecidable():
    """thenextweb.com returns 404 for its homepage, so its 404s mean nothing."""
    audit = audit_urls(
        {"https://soft404.test/some/article": "http_404"},
        {"https://soft404.test/some/article": "http_404"},
    )
    assert audit["absent_count"] == 0
    assert audit["undecidable_count"] == 1


def test_a_timeout_is_undecidable():
    audit = audit_urls(
        {"https://slow.test/a": "error:TimeoutError"},
        {"https://slow.test/a": "ok"},
    )
    assert audit["absent_count"] == 0
    assert audit["undecidable_count"] == 1


def test_counts_add_up():
    pairs = {
        "https://a.test/1": "ok",
        "https://b.test/2": "http_404",
        "https://c.test/3": "http_403",
        "https://d.test/4": "error:TimeoutError",
    }
    controls = {
        "https://a.test/1": "ok",
        "https://b.test/2": "ok",       # healthy host, so the 404 counts
        "https://c.test/3": "http_403",
        "https://d.test/4": "ok",
    }
    audit = audit_urls(pairs, controls)
    assert audit["probed"] == 4
    assert (audit["reachable_count"] + audit["absent_count"]
            + audit["undecidable_count"]) == 4


# --- the probe itself --------------------------------------------------------

def test_probing_nothing_is_safe():
    audit = probe_with_controls([])
    assert audit["probed"] == 0
    assert audit["absent"] == []


def test_the_control_is_actually_requested(monkeypatch):
    """The whole point is the extra request; assert it happens."""
    seen: list[str] = []
    real = provenance._probe_one

    def spy(url, timeout):
        seen.append(url)
        return real(url, timeout)

    monkeypatch.setattr(provenance, "_probe_one", spy)
    provenance.probe_with_controls(["https://a.test/article"], timeout=0.01)
    assert "https://a.test/article" in seen
    assert "https://a.test/" in seen, "the root control was never requested"
