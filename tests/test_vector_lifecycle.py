"""Vector index lifecycle: watcher sync, deletion cleanup and reindex purge."""

from __future__ import annotations

import os
import sqlite3

import pytest


@pytest.fixture()
def index_env(tmp_path, monkeypatch):
    """Isolated reports DB + fake embedding client."""
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    state = tmp_path / "state"
    output = tmp_path / "output"
    state.mkdir()
    output.mkdir()
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(state / "reports.db"))
    index_db.init_db()

    class FakeClient:
        # Mirrors core.embeddings.EmbeddingClient (no "enabled" attribute)
        model = "fake-model"

        def __init__(self):
            self.calls = []

        def embed(self, chunks):
            self.calls.append(list(chunks))
            return [[0.1] * 8 for _ in chunks]

    client = FakeClient()
    vector_sync.reset_client()
    monkeypatch.setattr(vector_sync, "_embedding_client", lambda: client)
    yield {"output": str(output), "state": str(state), "client": client}
    vector_sync.reset_client()


def _write_report(output_dir: str, rel: str, text: str = "# Title\n\nbody text\n") -> str:
    path = os.path.join(output_dir, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def test_sync_report_embeds_new_report(index_env):
    from web.indexer import vector_sync

    _write_report(index_env["output"], "research/a.md")
    result = vector_sync.sync_report(index_env["output"], "research/a.md")
    assert result["embedded"] > 0
    assert index_env["client"].calls


def test_sync_report_skips_unchanged(index_env):
    from web.indexer import vector_sync

    _write_report(index_env["output"], "research/a.md")
    vector_sync.sync_report(index_env["output"], "research/a.md")
    again = vector_sync.sync_report(index_env["output"], "research/a.md")
    assert again["skipped"] is True
    assert again["embedded"] == 0


def test_sync_report_embeds_after_content_change(index_env):
    from web.indexer import vector_sync

    rel = "research/a.md"
    _write_report(index_env["output"], rel, "# One\n\nfirst body\n")
    vector_sync.sync_report(index_env["output"], rel)
    _write_report(index_env["output"], rel, "# Two\n\nsecond body with more text\n")
    result = vector_sync.sync_report(index_env["output"], rel)
    assert result["embedded"] > 0


def test_remove_report_drops_vectors(index_env):
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    _write_report(index_env["output"], "research/a.md")
    vector_sync.sync_report(index_env["output"], "research/a.md")
    with index_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM report_chunks").fetchone()["c"] > 0

    result = vector_sync.remove_report("research/a.md")
    assert result["removed_chunks"] > 0
    with index_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM report_chunks").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM chunk_meta").fetchone()["c"] == 0


def test_purge_all_clears_store(index_env):
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    for i in range(2):
        rel = f"research/a{i}.md"
        _write_report(index_env["output"], rel)
        vector_sync.sync_report(index_env["output"], rel)

    purged = vector_sync.purge_all()
    assert purged["removed_chunks"] > 0
    with index_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) c FROM report_chunks").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM chunk_meta").fetchone()["c"] == 0


def test_coverage_reports_missing(index_env):
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    index_db.upsert_report("research/a.md", 100, 1.0, "A", "research")
    index_db.upsert_report("research/b.md", 100, 1.0, "B", "research")
    _write_report(index_env["output"], "research/a.md")
    vector_sync.sync_report(index_env["output"], "research/a.md")

    cov = vector_sync.coverage(index_env["state"])
    assert cov["total"] == 2
    assert cov["embedded"] == 1
    assert cov["missing"] == 1


def test_coverage_without_db(tmp_path):
    from web.indexer import vector_sync

    assert vector_sync.coverage(str(tmp_path))["total"] == 0


def test_sync_skips_when_embeddings_unavailable(index_env, monkeypatch):
    from web.indexer import vector_sync

    _write_report(index_env["output"], "research/a.md")
    monkeypatch.setattr(vector_sync, "_embedding_client", lambda: None)
    result = vector_sync.sync_report(index_env["output"], "research/a.md")
    assert result["skipped"] is True
    assert result["embedded"] == 0


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def test_reindex_endpoint_reports_coverage(client, web_env):
    tmp_path, _ = web_env
    client.post("/api/auth/login",
                json={"username": "admin", "password": "admin-pass-123"})
    out = tmp_path / "output"
    (out / "research").mkdir(parents=True, exist_ok=True)
    (out / "research" / "a.md").write_text("# A\n\nsome searchable body\n", encoding="utf-8")

    r = client.post("/api/qa/reindex?limit=5", headers=_csrf(client))
    assert r.status_code in (200, 422), r.text
    if r.status_code == 200:
        body = r.json()
        assert "coverage" in body and "purged" in body
    else:
        assert r.json()["error"]["code"] == "embeddings_not_configured"


def test_reindex_purge_flag_exists(client, web_env):
    client.post("/api/auth/login",
                json={"username": "admin", "password": "admin-pass-123"})
    r = client.post("/api/qa/reindex?purge=true&limit=1", headers=_csrf(client))
    assert r.status_code in (200, 422)


def test_client_gate_uses_model_not_enabled(index_env, monkeypatch):
    """Guard: EmbeddingClient has no ``enabled`` flag, only ``model``."""
    from web.indexer import vector_sync

    class ModelOnly:
        model = "bge-m3:latest"

        def embed(self, chunks):
            return [[0.2] * 8 for _ in chunks]

    monkeypatch.setattr(vector_sync, "_embedding_client", lambda: ModelOnly())
    _write_report(index_env["output"], "research/m.md")
    result = vector_sync.sync_report(index_env["output"], "research/m.md")
    assert result["embedded"] > 0
    assert result["skipped"] is False


def test_real_client_gate(monkeypatch):
    """The availability gate must accept a real EmbeddingClient instance."""
    from core.embeddings import EmbeddingClient
    from web.indexer import vector_sync

    real = EmbeddingClient(model="bge-m3:latest", base_url="http://localhost:11434/v1")
    assert not hasattr(real, "enabled")
    monkeypatch.setattr(vector_sync, "reset_client", vector_sync.reset_client)
    vector_sync.reset_client()

    def fake_from_system(_cfg):
        return real

    import core.embeddings as emb
    monkeypatch.setattr(emb.EmbeddingClient, "from_system", staticmethod(fake_from_system))
    got = vector_sync._embedding_client()
    assert got is real
    vector_sync.reset_client()


def test_prune_orphans_removes_stale_vectors(index_env):
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    index_db.upsert_report("research/a.md", 100, 1.0, "A", "research")
    _write_report(index_env["output"], "research/a.md")
    vector_sync.sync_report(index_env["output"], "research/a.md")
    assert vector_sync.coverage(index_env["state"])["orphan_vectors"] == 0

    # Simulate a leftover from an older version: vectors without a report row.
    with index_db.connect() as conn:
        conn.execute(
            "INSERT INTO chunk_meta (path, content_hash, model, embedded_at)"
            " VALUES ('research/gone.md', 'h', 'm', 1)"
        )

    coverage = vector_sync.coverage(index_env["state"])
    assert coverage["orphan_vectors"] == 1

    result = vector_sync.prune_orphans(index_env["output"], index_env["state"])
    assert result["orphan_documents"] == 1
    assert vector_sync.coverage(index_env["state"])["orphan_vectors"] == 0
    assert vector_sync.coverage(index_env["state"])["embedded"] == 1


def test_prune_keeps_live_documents(index_env):
    from web.indexer import db as index_db
    from web.indexer import vector_sync

    index_db.upsert_report("research/live.md", 100, 1.0, "Live", "research")
    _write_report(index_env["output"], "research/live.md")
    vector_sync.sync_report(index_env["output"], "research/live.md")
    result = vector_sync.prune_orphans(index_env["output"], index_env["state"])
    assert result["orphan_documents"] == 0
    assert vector_sync.coverage(index_env["state"])["embedded"] == 1
