"""Full scan of output/ directory to populate the reports index."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from web.indexer import db as index_db

logger = logging.getLogger("ai_research.web.indexer")

# Extensions to index
_INDEXED_EXT = {".md", ".mdx"}

# Common patterns to skip
_SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".DS_Store"}

# Upper bound for the FTS payload of one report (2 MB of text is far beyond a
# typical report; the cap only guards against pathological files).
MAX_INDEX_BYTES = 2 * 1024 * 1024


def extract_title(file_path: str) -> str:
    """Extract the first markdown heading (# title) from a file."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("# ") and len(line) > 2:
                    return line[2:].strip()
                if line.startswith("#"):  # h2-h6, not title
                    continue
                # Stop after first non-empty non-heading line
                if line:
                    break
    except OSError:
        pass
    return ""


def infer_job_name(rel_path: str) -> str:
    """Try to infer the originating job name from the file path.

    Heuristic: if a file is at output/monitoring/2026-06-09_report.md,
    the job is likely monitoring/... (based on directionary structure).
    """
    parts = Path(rel_path).parts
    if len(parts) >= 2:
        return parts[0]
    return ""


def index_file(output_dir: str, rel_path: str) -> bool:
    """Index a single file. Returns True if indexed, False on error/skip."""
    full_path = os.path.join(output_dir, rel_path)

    if not os.path.isfile(full_path):
        index_db.remove_report(rel_path)
        return True

    ext = os.path.splitext(rel_path)[1].lower()
    if ext not in _INDEXED_EXT:
        return False

    try:
        stat = os.stat(full_path)
        mtime = stat.st_mtime
        size = stat.st_size
        title = extract_title(full_path)
        category = Path(rel_path).parts[0] if len(Path(rel_path).parts) > 1 else ""
        job_name = infer_job_name(rel_path)

        # Read the document for full-text indexing. Reports used to be
        # truncated at 100KB and files >= 500KB were skipped entirely, so
        # their body was invisible to search.
        content = ""
        if size > 0:
            try:
                with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read(MAX_INDEX_BYTES)
            except OSError:
                pass

        index_db.upsert_report(
            path=rel_path,
            mtime=mtime,
            size_bytes=size,
            job_name=job_name,
            title=title,
            category=category,
            content=content,
        )
        return True
    except OSError as e:
        logger.warning("Failed to index %s: %s", rel_path, e)
        return False


def full_scan(output_dir: str) -> dict:
    """Walk entire output_dir and index all markdown files.

    Returns scan stats.
    """
    start = time.time()
    indexed = 0
    errors = 0
    skipped = 0

    output_path = Path(output_dir)
    if not output_path.is_dir():
        logger.warning("Output directory does not exist: %s", output_dir)
        return {"indexed": 0, "errors": 0, "skipped": 0, "elapsed": 0}

    for root, dirs, files in os.walk(output_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

        for fn in files:
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, output_path)

            if index_file(output_dir, rel):
                indexed += 1
            else:
                skipped += 1

    elapsed = time.time() - start
    logger.info("Full scan complete: %d indexed, %d skipped, %d errors in %.1fs",
                indexed, skipped, errors, elapsed)
    return {"indexed": indexed, "errors": errors, "skipped": skipped, "elapsed": round(elapsed, 1)}
