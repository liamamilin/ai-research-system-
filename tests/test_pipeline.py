"""Tests for search client helpers and pipeline retry/round logic."""

from __future__ import annotations

import os

from core.search import SearchClient, SearchResult

from web.runner import pipeline


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


def test_clean_queries_dedupes_and_caps():
    queries = ["  a  b ", "a b", "", "c", "d", "e", "f", "g"]
    cleaned = SearchClient._clean_queries(queries)
    assert cleaned == ["a b", "c", "d", "e", "f"]  # max 5, deduped, trimmed


def test_dedupe_by_url_keeps_first():
    results = [
        SearchResult(title="a", url="https://x.test/1", excerpts=["1"]),
        SearchResult(title="b", url="https://x.test/1/", excerpts=["2"]),
        SearchResult(title="c", url="https://y.test/2", excerpts=["3"]),
    ]
    deduped = SearchClient._dedupe(results)
    assert [r.title for r in deduped] == ["a", "c"]


def test_format_results_renders_markdown():
    text = SearchClient.format_results(
        [SearchResult(title="T", url="https://x", publish_date="2026-01-01",
                      excerpts=["body"])]
    )
    assert "https://x" in text and "body" in text and "1 sources" in text


# ---------------------------------------------------------------------------
# Pipeline round logic
# ---------------------------------------------------------------------------


def _file_stages(existing: set[str]) -> dict:
    return {
        key: {"key": key, "exists": key in existing}
        for key, _, _ in pipeline.STAGES
    }


def test_retry_skipped_stages_are_not_rerun():
    """Regression: 'skipped' (already complete in an earlier retry) must not
    trigger a rerun, otherwise complete rounds get re-executed."""
    previous = {
        key: {"status": "skipped"} for key, _, _ in pipeline.STAGES
    }
    previous["06_infra_and_eval_radar"] = {"status": "success"}
    existing = {key for key, _, _ in pipeline.STAGES}
    assert pipeline._stages_needing_rerun(_file_stages(existing), previous) == []


def test_retry_picks_missing_and_failed_only():
    previous = {"01_model_and_pricing_radar": {"status": "failed"}}
    existing = {key for key, _, _ in pipeline.STAGES
                if key not in ("06_infra_and_eval_radar", "01_model_and_pricing_radar")}
    needed = pipeline._stages_needing_rerun(_file_stages(existing), previous)
    assert needed == ["01_model_and_pricing_radar", "06_infra_and_eval_radar"]


def test_build_round_files_reports_existence(tmp_path):
    round_dir = tmp_path / pipeline.PIPELINE_DIR / "2026-01-02"
    round_dir.mkdir(parents=True)
    (round_dir / "00_collection_plan.md").write_text("x", encoding="utf-8")

    stages = pipeline.build_round_files("2026-01-02", str(tmp_path))
    by_key = {s["key"]: s for s in stages}
    assert by_key["00_collection_planner"]["exists"] is True
    assert by_key["09_executive_synthesis_and_actions"]["exists"] is False
    assert os.path.basename(by_key["00_collection_planner"]["file"]).startswith("00_")


# ---------------------------------------------------------------------------
# Search provider adapters (parsing/payload)
# ---------------------------------------------------------------------------


def test_tavily_adapter_parses_and_uses_body_key(monkeypatch):
    from core.search import SearchConfig

    captured = {}

    def fake_post(url, payload, api_key=None, header_name="x-api-key"):
        captured.update({"url": url, "payload": payload})
        return {"results": [{"title": "T", "url": "https://x",
                             "content": "body", "published_date": "2026-01-01"}]}

    cfg = SearchConfig(provider="tavily",
                       api_keys={"tavily": "key-123"},
                       max_results=5)
    client = SearchClient(cfg)
    monkeypatch.setattr(client, "_post_json", fake_post)

    results = client._search_tavily(["deepseek pricing"], "objective")
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["payload"]["api_key"] == "key-123"
    assert results[0].provider == "tavily"
    assert results[0].publish_date == "2026-01-01"
    assert results[0].excerpts == ["body"]


def test_serper_adapter_parses(monkeypatch):
    from core.search import SearchConfig

    def fake_post(url, payload, api_key=None, header_name="x-api-key"):
        assert url == "https://google.serper.dev/search"
        assert api_key == "sk-serper"
        return {"organic": [{"title": "S", "link": "https://y",
                             "snippet": "snip", "date": "Jan 2, 2026"}]}

    cfg = SearchConfig(provider="serper", api_keys={"serper": "sk-serper"})
    client = SearchClient(cfg)
    monkeypatch.setattr(client, "_post_json", fake_post)

    results = client._search_serper(["query"])
    assert results[0].url == "https://y"
    assert results[0].provider == "serper"


def test_missing_provider_key_raises():
    from core.search import SearchConfig

    cfg = SearchConfig(provider="tavily", api_keys={})
    client = SearchClient(cfg)
    try:
        client._provider_key("tavily")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "missing API key" in str(exc)
