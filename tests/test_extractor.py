"""The post-processing step that turns raw model output into the final report.

`extract_report` sits between the agent's answer and the file on disk, and its
failure mode is quiet by design: anything that goes wrong returns the original
content unchanged. That is the right call for a report -- a worse report is
worse than an untidy one -- but it also means every guard in here can be deleted
and nothing will fail loudly. The report would simply get worse.

So these tests pin the guards themselves, not just the happy path.
"""

from __future__ import annotations

import threading

import pytest

from core import extractor

RAW = "# 报告\n\n" + "这是原始内容。" * 40


def _config(**overrides) -> dict:
    cfg = {
        "enabled": True,
        "model": "cleaner-1",
        "base_url": "https://api.example/v1/",
        "api_key_env": "TEST_EXTRACTION_KEY",
    }
    cfg.update(overrides)
    return {"extraction": cfg}


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("TEST_EXTRACTION_KEY", "sk-test")
    monkeypatch.delenv("EXTRACTION_API_KEY", raising=False)


# --- configuration guards ----------------------------------------------------

def test_disabled_extraction_returns_the_original(monkeypatch):
    called = []
    monkeypatch.setattr(extractor, "_call_extraction_api",
                        lambda *a, **k: called.append(1))
    out = extractor.extract_report(RAW, {"extraction": {"enabled": False}})
    assert out == RAW
    assert called == [], "a disabled extractor must not reach the API"


def test_missing_config_returns_the_original():
    assert extractor.extract_report(RAW, {}) == RAW
    assert extractor.extract_report(RAW, {"extraction": {}}) == RAW


def test_a_config_without_model_or_base_url_is_refused(monkeypatch):
    # Half-configured extraction used to raise on the first run instead of
    # declining here, which took the report down with it.
    called = []
    monkeypatch.setattr(extractor, "_call_extraction_api",
                        lambda *a, **k: called.append(1))
    for broken in ({"model": None}, {"base_url": None}, {"model": "", "base_url": ""}):
        assert extractor.extract_report(RAW, _config(**broken)) == RAW
    assert called == []


def test_a_missing_api_key_disables_extraction(monkeypatch):
    monkeypatch.delenv("TEST_EXTRACTION_KEY", raising=False)
    assert extractor.extract_report(RAW, _config()) == RAW
    # And it says which variable is missing, rather than failing opaquely.
    assert extractor._load_extraction_config(_config()) is None


def test_base_url_loses_its_trailing_slash():
    cfg = extractor._load_extraction_config(_config())
    assert cfg["base_url"] == "https://api.example/v1"


# --- short and cancelled input ----------------------------------------------

def test_short_content_is_not_worth_an_api_call(monkeypatch):
    called = []
    monkeypatch.setattr(extractor, "_call_extraction_api",
                        lambda *a, **k: called.append(1))
    assert extractor.extract_report("# 太短", _config()) == "# 太短"
    assert called == []


def test_a_cancelled_run_does_not_call_the_api(monkeypatch):
    called = []
    monkeypatch.setattr(extractor, "_call_extraction_api",
                        lambda *a, **k: called.append(1))
    token = threading.Event()
    token.set()
    assert extractor.extract_report(RAW, _config(), cancel_token=token) == RAW
    assert called == [], "cancelled work must not be paid for"


# --- the success path, and every way it can decline --------------------------

def test_cleaned_content_is_returned(monkeypatch):
    cleaned = "# 报告\n\n" + "整理后的内容。" * 40
    monkeypatch.setattr(extractor, "_call_extraction_api",
                        lambda raw, cfg: cleaned)
    assert extractor.extract_report(RAW, _config()) == cleaned


def test_an_api_failure_keeps_the_original(monkeypatch):
    monkeypatch.setattr(extractor, "_call_extraction_api", lambda raw, cfg: None)
    assert extractor.extract_report(RAW, _config()) == RAW


def test_a_suspiciously_short_result_is_discarded(monkeypatch):
    # An extractor that returns almost nothing is a failure wearing a success
    # shape; keeping it would replace a real report with a stub.
    monkeypatch.setattr(extractor, "_call_extraction_api", lambda raw, cfg: "太短")
    assert extractor.extract_report(RAW, _config()) == RAW


def test_a_raising_api_is_contained(monkeypatch):
    def boom(raw, cfg):
        raise RuntimeError("upstream exploded")

    monkeypatch.setattr(extractor, "_call_extraction_api", boom)
    # A cleanup step must never be the reason a report is lost.
    assert extractor.extract_report(RAW, _config()) == RAW
