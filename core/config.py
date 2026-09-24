"""
Unified configuration loading.
Loads system.yaml and job YAML files (flat or nested).
"""

import os
import logging
import yaml
from typing import Optional

logger = logging.getLogger(__name__)

# Directories to skip during job discovery
_SKIP_DIRS = {"_templates", "__pycache__", ".git", "node_modules"}


def load_system_config(config_dir: str = "config") -> dict:
    """Load system.yaml configuration."""
    path = os.path.join(config_dir, "system.yaml")
    if not os.path.exists(path):
        logger.warning("system.yaml not found at %s, using defaults", path)
        return {}
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    logger.debug("Loaded system config from %s", path)
    return cfg


def _is_safe_job_name(job_name: str) -> bool:
    """Reject names that could escape the jobs directory."""
    if not job_name or job_name.startswith(("/", "\\")):
        return False
    if os.path.isabs(job_name):
        return False
    normalized = os.path.normpath(job_name)
    if normalized.startswith("..") or normalized.startswith("/"):
        return False
    parts = normalized.replace("\\", "/").split("/")
    return ".." not in parts


def _find_job_path(jobs_dir: str, job_name: str) -> Optional[str]:
    """Resolve a job name to its file path, searching recursively.

    Supports flat names ("simple_test") and nested names ("monitoring/ai_monitoring").
    Also tries matching against the YAML 'name' field (case-insensitive).
    Returns the file path relative to jobs_dir (without extension) if found.

    Names that would escape ``jobs_dir`` (absolute paths, ``..`` segments) are
    rejected so a crafted job name cannot read or write files elsewhere.
    """
    if not _is_safe_job_name(job_name):
        logger.warning("Rejected unsafe job name: %r", job_name)
        return None

    jobs_root = os.path.abspath(jobs_dir)

    def _within(path: str) -> bool:
        return os.path.abspath(path).startswith(jobs_root + os.sep)

    # Direct lookup: try exact path
    candidate = os.path.join(jobs_dir, f"{job_name}.yaml")
    if os.path.isfile(candidate) and _within(candidate):
        return job_name

    candidate_yml = os.path.join(jobs_dir, f"{job_name}.yml")
    if os.path.isfile(candidate_yml) and _within(candidate_yml):
        return job_name

    # Recursive search by basename (for backward compat: "ai_monitoring" → find anywhere)
    # Also match against the 'name' field inside YAML files.
    for root, dirs, files in os.walk(jobs_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith("_")]
        for fn in files:
            if not fn.endswith((".yaml", ".yml")):
                continue
            file_path = os.path.join(root, fn)
            rel = os.path.relpath(file_path, jobs_dir)
            rel_no_ext = rel.rsplit(".", 1)[0]

            # 1) Exact match by relative path
            if rel_no_ext == job_name:
                return rel_no_ext
            # 2) Match by basename (e.g., "ai_monitoring" finds "monitoring/ai_monitoring")
            base = fn.rsplit(".", 1)[0]
            if base == job_name:
                return rel_no_ext
            # 3) Match by internal 'name' field (case-insensitive, first word heuristic)
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                file_job_name = data.get("name", "")
                if file_job_name.lower() == job_name.lower():
                    return rel_no_ext
            except Exception:
                pass

    return None


def load_job(jobs_dir: str, job_name: str) -> Optional[dict]:
    """Load a single job YAML file by name or path. Returns None if not found."""
    resolved = _find_job_path(jobs_dir, job_name)
    if resolved is None:
        logger.debug("Job not found: %s", job_name)
        return None

    path = os.path.join(jobs_dir, f"{resolved}.yaml")
    # Try .yml extension if .yaml doesn't exist
    if not os.path.isfile(path):
        path_yml = os.path.join(jobs_dir, f"{resolved}.yml")
        if os.path.isfile(path_yml):
            path = path_yml

    with open(path, "r", encoding="utf-8") as f:
        job = yaml.safe_load(f) or {}
    job["_file"] = resolved
    try:
        job["_mtime"] = os.path.getmtime(path)
    except OSError:
        job["_mtime"] = 0.0
    return job


def list_jobs(jobs_dir: str, enabled_only: bool = False) -> list:
    """List all job names (relative paths without extension) in the jobs directory.

    Skips directories starting with '_' (e.g., _templates) and special dirs.
    """
    if not os.path.isdir(jobs_dir):
        return []

    jobs = []
    for root, dirs, files in os.walk(jobs_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith("_")]
        for fn in sorted(files):
            if not fn.endswith((".yaml", ".yml")):
                continue
            if fn.startswith("."):
                continue
            file_path = os.path.join(root, fn)
            rel = os.path.relpath(file_path, jobs_dir)
            name = rel.rsplit(".", 1)[0]

            if enabled_only:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        job = yaml.safe_load(f) or {}
                    if not job.get("enabled", True):
                        continue
                except Exception:
                    continue

            jobs.append(name)

    return jobs
