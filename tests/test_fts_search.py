"""Chinese full-text search: trigram tokenizer, migration and fallbacks."""

from __future__ import annotations

import os
import sqlite3

import pytest

from web.indexer import db as index_db
from web.indexer import scanner as index_scanner


@pytest.fixture()
def index_db_env(tmp_path, monkeypatch):
    monkeypatch.setattr(index_db, "_DB_PATH_OVERRIDE", str(tmp_path / "reports.db"))
    index_db.init_db()
    output = tmp_path / "output"
    output.mkdir()
    yield output
    index_db.set_db_path(None)


def _write(output, rel, text):
    path = output / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


# --- tokenizer behaviour ----------------------------------------------------


def test_chinese_phrase_search(index_db_env):
    _write(index_db_env, "research/a.md", "# 独立游戏发行\n\n独立 游戏的 定价 变化\n")
    _write(index_db_env, "research/b.md", "# 模型定价\n\nOpenAI 的价格调整\n")
    index_scanner.index_file(str(index_db_env), "research/a.md")
    index_scanner.index_file(str(index_db_env), "research/b.md")

    hits = index_db.search_reports("独立游戏", limit=10)
    assert [h["path"] for h in hits] == ["research/a.md"]

    # 2-character terms cannot use trigrams: they fall back to a LIKE scan,
    # which covers titles/paths (the body text lives only in the FTS index),
    # so only the document with 定价 in its title matches.
    assert [h["path"] for h in index_db.search_reports("定价", limit=10)] == ["research/b.md"]


def test_english_search_still_works(index_db_env):
    _write(index_db_env, "research/a.md", "# Prompt caching\n\nEnable prompt caching for the system prefix.\n")
    index_scanner.index_file(str(index_db_env), "research/a.md")
    assert index_db.search_reports("prompt caching", limit=10)
    assert index_db.search_reports("caching", limit=10)


def test_short_query_falls_back_to_like(index_db_env):
    """Trigrams cannot match 1-2 character terms, so those use LIKE."""
    _write(index_db_env, "research/ai.md", "# AI 工程\n\n大量关于 AI 的内容\n")
    _write(index_db_env, "research/rag.md", "# RAG 检索\n\n检索增强生成\n")
    index_scanner.index_file(str(index_db_env), "research/ai.md")
    index_scanner.index_file(str(index_db_env), "research/rag.md")

    assert [h["path"] for h in index_db.search_reports("AI", limit=10)] == ["research/ai.md"]
    assert [h["path"] for h in index_db.search_reports("RAG", limit=10)] == ["research/rag.md"]


def test_mixed_short_and_long_terms_search_both(index_db_env):
    """One short term must not throw away the trigram search for the rest.

    "AI 检索增强" used to go down the LIKE path as a single phrase, so it
    matched neither the body containing 检索增强 nor the title containing AI.
    """
    # Found by trigram only: the short term is absent from its title and path.
    _write(index_db_env, "research/memory.md", "# 记忆系统\n\n检索增强生成\n")
    # Found by LIKE only: the long term appears nowhere in it.
    _write(index_db_env, "research/ai.md", "# AI 工程\n\n完全不同的内容\n")
    # Neither.
    _write(index_db_env, "research/other.md", "# 无关报告\n\n别的说法\n")
    for rel in ("memory.md", "ai.md", "other.md"):
        index_scanner.index_file(str(index_db_env), f"research/{rel}")

    hits = index_db.search_reports("AI 检索增强", limit=10)
    # The trigram match (body text) leads; the LIKE match (title/path) follows.
    assert [h["path"] for h in hits] == ["research/memory.md", "research/ai.md"]
    assert hits[0]["fts_score"] is not None
    assert hits[1]["fts_score"] is None
    assert "research/other.md" not in [h["path"] for h in hits]


def test_short_terms_are_or_ed_not_phrase_matched(index_db_env):
    _write(index_db_env, "research/a.md", "# 定价模型\n\n正文无关\n")
    _write(index_db_env, "research/b.md", "# 模型评估\n\n正文无关\n")
    for rel in ("a.md", "b.md"):
        index_scanner.index_file(str(index_db_env), f"research/{rel}")

    hits = index_db.search_reports("定价 评估", limit=10)
    assert {h["path"] for h in hits} == {"research/a.md", "research/b.md"}


def test_like_term_count_is_capped(index_db_env):
    """A pasted paragraph must not build an unbounded SQL statement."""
    _write(index_db_env, "research/a.md", "# 报告\n\n正文\n")
    index_scanner.index_file(str(index_db_env), "research/a.md")
    # 30 two-character terms, only the first 8 are scanned.
    query = " ".join("关键词" for _ in range(30))
    assert index_db.search_reports(query, limit=10) == []


def test_empty_query_returns_nothing(index_db_env):
    assert index_db.search_reports("", limit=10) == []
    assert index_db.search_reports("   ", limit=10) == []


# --- migration --------------------------------------------------------------


def test_unicode61_index_is_migrated_and_rescanned(index_db_env):
    """An index built with the old tokenizer must be rebuilt, not left stale."""
    path = index_db.db_path()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        DROP TABLE IF EXISTS reports_fts;
        CREATE VIRTUAL TABLE reports_fts USING fts5(title, content, tokenize='unicode61');
        """
    )
    conn.execute("INSERT INTO reports_fts (rowid, title, content) VALUES (1, '旧', '内容')")
    conn.commit()
    conn.close()

    result = index_db.init_db()
    assert result["fts_rebuilt"] is True

    with index_db.connect() as c:
        sql = c.execute(
            "SELECT sql FROM sqlite_master WHERE name='reports_fts'").fetchone()["sql"]
        assert "trigram" in sql
        assert c.execute("SELECT COUNT(*) FROM reports_fts").fetchone()[0] == 0

    # A second init must be a no-op
    assert index_db.init_db()["fts_rebuilt"] is False


def test_migration_preserves_report_metadata(index_db_env):
    _write(index_db_env, "research/keep.md", "# 保留\n\n星标与标签不能丢\n")
    index_scanner.index_file(str(index_db_env), "research/keep.md")
    index_db.set_report_meta("research/keep.md", favorite=True, tags=["重要"],
                             read=True, rating=4)

    # Simulate the old tokenizer so init_db has to migrate.
    with sqlite3.connect(index_db.db_path()) as c:
        c.execute("DROP TABLE reports_fts")
        c.execute("CREATE VIRTUAL TABLE reports_fts USING fts5(title, content, tokenize='unicode61')")
    assert index_db.init_db()["fts_rebuilt"] is True
    index_scanner.index_file(str(index_db_env), "research/keep.md")

    row = index_db.get_report("research/keep.md")
    assert row["favorite"] is True
    assert row["tags"] == ["重要"]
    assert row["read"] is True
    assert row["rating"] == 4
    assert index_db.search_reports("保留", limit=5)


# --- large documents --------------------------------------------------------


def test_large_report_body_is_indexed(index_db_env):
    """Reports >= 500KB used to be skipped entirely, and all were cut at 100KB."""
    filler = "占位内容。" * 200
    deep_tail = "深层结论：向量数据库与提示缓存的组合显著降低成本。"
    body = filler * 400 + "\n" + deep_tail
    assert len(body.encode("utf-8")) > 500_000
    _write(index_db_env, "research/big.md", f"# 大报告\n\n{body}\n")
    index_scanner.index_file(str(index_db_env), "research/big.md")

    assert index_db.search_reports("深层结论", limit=5), "tail of a large report must be searchable"
    assert index_db.search_reports("向量数据库", limit=5)


def test_very_large_file_is_capped_not_skipped(index_db_env, monkeypatch):
    monkeypatch.setattr(index_scanner, "MAX_INDEX_BYTES", 200)
    _write(index_db_env, "research/huge.md", "# 超大\n" + "内容" * 5000)
    assert index_scanner.index_file(str(index_db_env), "research/huge.md") is True
    assert index_db.get_report("research/huge.md") is not None


def test_snippet_is_returned_for_fts_hits(index_db_env):
    _write(index_db_env, "research/a.md", "# 标题\n\n这里提到了提示缓存的细节说明。\n")
    index_scanner.index_file(str(index_db_env), "research/a.md")
    hits = index_db.search_reports("提示缓存", limit=5)
    assert hits and "<mark>" in (hits[0]["snippet"] or "")


def test_prune_and_remove_keep_fts_consistent(index_db_env):
    rel = "research/gone.md"
    _write(index_db_env, rel, "# 消失\n\n提示缓存内容\n")
    index_scanner.index_file(str(index_db_env), rel)
    assert index_db.search_reports("提示缓存", limit=5)
    os.remove(index_db_env / rel)
    index_db.remove_report(rel)
    assert index_db.search_reports("提示缓存", limit=5) == []
