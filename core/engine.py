"""
Research Engine - core workflow orchestrator.

Loads job config, renders the prompt, runs the research agent
(LLM API + web search API), and saves the report.
"""

import logging
import os
import re
import time
import threading
from datetime import datetime, timedelta
from typing import Optional

from .config import load_system_config, load_job, list_jobs
from .errors import CancelledError
from .extractor import extract_report
from .fileio import atomic_write
from .llm import LLMConfig
from .lock import LockManager, LockAcquireError
from .research import ResearchAgent, ResearchConfig
from .search import SearchConfig
from .state import StateManager

logger = logging.getLogger(__name__)

__all__ = ["ResearchEngine", "CancelledError"]


class ResearchEngine:
    """Core engine for running research jobs."""

    def __init__(self, config_dir: str = "config", jobs_dir: str = "jobs",
                 cancel_token: Optional[threading.Event] = None,
                 progress_cb=None, workspace_dir: Optional[str] = None,
                 date_override: Optional[str] = None,
                 user: Optional[str] = None):
        self.config_dir = config_dir
        self.jobs_dir = jobs_dir
        self.sys = load_system_config(config_dir)
        self._cancel = cancel_token or threading.Event()
        self._progress = progress_cb or (lambda _: None)
        self.workspace_dir = os.path.abspath(workspace_dir or os.getcwd())
        self._date_override = date_override
        self.user = user or ""

    def _now(self) -> datetime:
        """Current time, with the date replaced by date_override when set.

        Used to backfill a past pipeline round: prompts and output paths
        resolve {date} to that round's date instead of today.
        """
        now = datetime.now()
        if self._date_override:
            try:
                target = datetime.strptime(self._date_override, "%Y-%m-%d")
                return now.replace(year=target.year, month=target.month,
                                   day=target.day)
            except ValueError:
                logger.warning("Invalid date_override: %s", self._date_override)
        return now

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _notify(self, event: str, title: str, message: str,
                fields: Optional[dict] = None) -> None:
        """Best-effort webhook notification (never raises)."""
        try:
            from .notify import send

            send(event, title, message, fields=fields, sys_config=self.sys)
        except Exception:  # noqa: BLE001 - notifications are optional
            logger.debug("Notification skipped for %s", title)

    def run_job(self, job_name: str, verbose: bool = False) -> Optional[str]:
        """Run a single job. Returns output path on success, None on failure."""
        job = load_job(self.jobs_dir, job_name)
        if not job:
            logger.info("Job not found: %s", job_name)
            print(f"  [{job_name}] NOT FOUND: no matching job file")
            return None

        if not job.get("enabled", True):
            logger.info("Job '%s' is disabled, skipping.", job_name)
            print(f"  [{job_name}] SKIPPED: job is disabled (enabled: false)")
            return None

        logger.info("Running job: %s", job_name)
        desc = job.get("description", "")
        print(f"  [{job_name}] {desc}")

        # --- Lock: prevent concurrent execution ---
        runtime_cfg = job.get("runtime", {})
        timeout = runtime_cfg.get("timeout_seconds",
                                  self.sys.get("ai", {}).get("timeout", 1800))
        stale_after = timeout + 300  # 5 min buffer for crash recovery

        start_time = time.time()
        output_path = None
        prev_path: Optional[str] = None
        agent: Optional[ResearchAgent] = None
        usage: Optional[dict] = None

        try:
            with LockManager(job.get("_file", job_name), stale_after=stale_after):

                if self._cancel.is_set():
                    raise CancelledError("Job cancelled before start")

                self._progress({"type": "phase", "phase": "building_prompt"})
                print(f"  [{job_name}] Building prompt...")
                output_path = self._resolve_output_path(job)
                prompt = self._build_prompt(job)
                source_block = self._fetch_sources_block(job)
                if source_block:
                    prompt = f"{prompt}\n\n{source_block}"
                logger.debug("Prompt built: %d chars", len(prompt))
                if verbose:
                    logger.debug("Prompt preview:\n%s", prompt[:500])

                if self._cancel.is_set():
                    raise CancelledError("Job cancelled before research")

                print(f"  [{job_name}] Researching (LLM + web search)...")

                # Move stale output aside: a failed/cancelled run must not
                # destroy the previous report, but a successful run must not
                # leave two versions behind either (restored in `finally`).
                prev_path = output_path + ".prev"
                try:
                    if os.path.exists(prev_path):
                        os.remove(prev_path)
                    if os.path.exists(output_path):
                        os.replace(output_path, prev_path)
                except OSError as exc:
                    logger.warning("Could not stash stale output: %s", exc)
                    prev_path = None

                agent = ResearchAgent(
                    llm_config=LLMConfig.from_system(self.sys, "ai"),
                    search_config=SearchConfig.from_system(self.sys),
                    research_config=ResearchConfig.from_system(self.sys),
                    workspace_dir=self.workspace_dir,
                    cancel_token=self._cancel,
                    progress_cb=self._progress,
                    timeout_seconds=timeout,
                )
                objective = " - ".join(
                    part for part in (job.get("name", ""), desc) if part
                )
                raw_content = agent.run(
                    prompt,
                    objective=objective,
                    fallback_queries=job.get("keywords", []),
                )
                usage = agent.get_usage()

                if self._cancel.is_set():
                    raise CancelledError("Job cancelled after research")

                self._progress({"type": "phase", "phase": "saving_output"})
                print(f"  [{job_name}] Saving output...")

                if not raw_content or not raw_content.strip():
                    logger.error("No output content for job '%s'", job_name)
                    print(f"  [{job_name}] FAILED: model returned no content")
                    StateManager.update(job_name, "failed",
                                        error="Model returned no content",
                                        usage=usage,
                                        user=self.user,
                                        duration_seconds=round(
                                            time.time() - start_time, 1))
                    self._notify("failed", f"Job 失败: {job_name}",
                                 "模型未返回内容",
                                 fields={"job": job_name, "status": "failed"})
                    return None

                cleaned = extract_report(raw_content, self.sys,
                                         cancel_token=self._cancel)
                actual_path = self._save_output(cleaned, output_path)

                citation = self._check_citations(cleaned, agent)
                self._register_events(job_name, actual_path, cleaned)
                try:
                    from .report_meta import append_record

                    append_record(actual_path, job_name, usage=usage,
                                  duration_seconds=round(time.time() - start_time, 1),
                                  extra={"citation_check": citation})
                except Exception as exc:  # noqa: BLE001 - metadata is optional
                    logger.debug("report metadata skipped: %s", exc)
                logger.info(
                    "Job '%s' completed → %s (%d bytes)",
                    job_name, actual_path, len(cleaned),
                )
                print(f"  [{job_name}] Done → {actual_path}")
                output_path = actual_path  # use actual path for state

                elapsed = time.time() - start_time
                self._progress({"type": "status", "status": "success",
                                "output_path": output_path,
                                "duration_seconds": round(elapsed, 1)})
                StateManager.update(job_name, "success",
                                    output_path=output_path,
                                    usage=usage,
                                    user=self.user,
                                    duration_seconds=round(elapsed, 1))
                tokens = (usage or {}).get("total_tokens")
                self._notify(
                    "success", f"Job 完成: {job_name}",
                    f"{round(elapsed, 1)}s"
                    + (f" · {tokens:,} tokens" if tokens else "")
                    + (f" · {output_path}" if output_path else ""),
                    fields={"job": job_name, "status": "success",
                            "output": output_path, "duration_seconds": round(elapsed, 1),
                            "usage": usage},
                )
                return output_path
        except LockAcquireError:
            print(f"  [{job_name}] SKIPPED: already running (lock held)")
            StateManager.update(job_name, "skipped",
                                error="Already running (lock held)")
            return None
        except CancelledError:
            usage = usage or (agent.get_usage() if agent else None)
            elapsed = time.time() - start_time
            logger.info("Job '%s' cancelled after %.1fs", job_name, elapsed)
            print(f"  [{job_name}] CANCELLED")
            self._progress({"type": "status", "status": "cancelled",
                            "duration_seconds": round(elapsed, 1)})
            StateManager.update(job_name, "cancelled",
                                output_path=output_path,
                                error="Cancelled by user",
                                usage=usage,
                                user=self.user,
                                duration_seconds=round(elapsed, 1))
            self._notify("cancelled", f"Job 取消: {job_name}",
                         f"运行 {round(elapsed, 1)}s 后被取消",
                         fields={"job": job_name, "status": "cancelled"})
            return None
        except TimeoutError as e:
            usage = usage or (agent.get_usage() if agent else None)
            elapsed = time.time() - start_time
            logger.error("Job '%s' timed out after %.1fs: %s",
                         job_name, elapsed, e)
            print(f"  [{job_name}] FAILED: {e}")
            StateManager.update(job_name, "failed",
                                output_path=output_path,
                                error=str(e),
                                usage=usage,
                                duration_seconds=round(elapsed, 1))
            self._notify("failed", f"Job 失败: {job_name}", str(e)[:300],
                         fields={"job": job_name, "status": "failed"})
            return None
        except Exception as e:
            usage = usage or (agent.get_usage() if agent else None)
            elapsed = time.time() - start_time
            logger.error("Job '%s' failed: %s", job_name, e)
            print(f"  [{job_name}] FAILED: {e}")
            if verbose:
                logger.exception("Details:")
            StateManager.update(job_name, "failed",
                                output_path=output_path,
                                error=str(e),
                                usage=usage,
                                duration_seconds=round(elapsed, 1))
            self._notify("failed", f"Job 失败: {job_name}", str(e)[:300],
                         fields={"job": job_name, "status": "failed"})
            return None
        finally:
            # Restore the stashed report when this run produced no new file.
            # "Produced a new file" means non-empty: _save_output is atomic now,
            # so a zero-byte file at output_path can only be a leftover, and
            # treating it as success would delete the good report we stashed.
            if prev_path and output_path:
                try:
                    if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                        os.remove(prev_path)   # fresh report saved
                    elif os.path.exists(prev_path):
                        os.replace(prev_path, output_path)
                        logger.info("Restored previous report: %s", output_path)
                except OSError as exc:
                    logger.warning("Could not restore stashed report: %s", exc)
            if agent is not None:
                agent.close()

    def run_all(self, verbose: bool = False) -> dict:
        """Run all enabled jobs. Returns {job_name: output_path or None}."""
        jobs = list_jobs(self.jobs_dir, enabled_only=True)
        results = {}
        for i, name in enumerate(jobs):
            if i > 0:
                print()  # blank line between jobs
            results[name] = self.run_job(name, verbose=verbose)
        return results

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def _fetch_sources_block(self, job: dict) -> str:
        """Pre-fetch job ``sources`` (RSS/GitHub/arXiv/HN) into prompt context."""
        specs = job.get("sources") or []
        if not specs:
            return ""
        try:
            from .search import SearchClient
            from .sources import fetch_all

            limit = int(job.get("sources_limit", 8))
            results = fetch_all(list(specs), limit_per_source=limit)
            if not results:
                return ""
            logger.info("Sources pre-fetched: %d items from %d spec(s)",
                        len(results), len(specs))
            return ("## Pre-fetched subscription sources\n\n"
                    + SearchClient.format_results(results))
        except Exception as exc:  # noqa: BLE001 - sources are optional context
            logger.warning("Sources pre-fetch skipped: %s", exc)
            return ""

    def _build_prompt(self, job: dict) -> str:
        """Render the prompt template with job variables."""
        template = job.get("prompt", "")
        if not template:
            raise ValueError(f"Job '{job.get('name', job.get('_file'))}' has no prompt.")

        keywords = job.get("keywords", [])
        keyword_str = ", ".join(keywords) if keywords else ""

        now = self._now()
        variables = {
            "name": job.get("name", job.get("_file", "")),
            "keywords": keyword_str,
            "language": job.get("language", "zh"),
            "date": now.strftime("%Y-%m-%d"),
            "date_7d_ago": (now - timedelta(days=7)).strftime("%Y-%m-%d"),
            "date_1d_ago": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
            "time": now.strftime("%H-%M-%S"),
            "datetime": now.strftime("%Y-%m-%d_%H-%M-%S"),
            "recent_outcomes": self._outcomes_block(),
            "reported_events": self._reported_events(),
        }
        pass

        # Simple {var} substitution
        result = template
        for key, val in variables.items():
            result = result.replace("{" + key + "}", str(val))

        return result

    def _reported_events(self) -> str:
        """Sources already reported in recent rounds (suppress duplicates)."""
        try:
            sys_cfg = getattr(self, "sys", None) or {}
            cfg = (sys_cfg.get("research") or {}).get("event_memory") or {}
            if cfg.get("enabled", True) is False:
                return ""
            from . import events

            events.use_state_dir(
                os.path.join(getattr(self, "workspace_dir", "."), "state"))
            return events.reported_block(
                days=int(cfg.get("days", 7)),
                limit=int(cfg.get("limit", 15)),
            )
        except Exception:  # noqa: BLE001 - never break a run over memory
            return ""

    @staticmethod
    def _outcomes_block() -> str:
        """Recent done/dropped action outcomes, for prompt feedback loops."""
        try:
            from core import tracking

            return tracking.format_outcomes()
        except Exception:  # noqa: BLE001 - never break a run over tracking
            return ""

    @staticmethod
    def _round_date_from_path(path: str) -> Optional[str]:
        """Round date encoded in an output path (…/practical_ai_intelligence/YYYY-MM-DD/…)."""
        m = re.search(r"(\d{4}-\d{2}-\d{2})", str(path or ""))
        return m.group(1) if m else None

    def _register_events(self, job_name: str, output_path: str, content: str) -> Optional[dict]:
        """Record the report's sources in the cross-round event memory."""
        try:
            from . import events

            events.use_state_dir(os.path.join(self.workspace_dir, "state"))
            counts = events.register_report(
                job_name, self._now().strftime("%Y-%m-%d"), content,
                round_date=self._round_date_from_path(output_path),
            )
            if counts.get("clustered"):
                logger.info(
                    "event memory: %s new source(s), %s folded into known events",
                    counts.get("new", 0), counts["clustered"],
                )
            return counts
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not break runs
            logger.debug("event registration skipped: %s", exc)
            return None

    def _check_citations(self, content: str, agent) -> Optional[dict]:
        """Verify the report's URLs against what this run actually retrieved.

        Configured via ``research.citation_check`` in system.yaml; failures are
        logged and recorded in report metadata, never fatal to the run.
        """
        cfg = (self.sys.get("research") or {}).get("citation_check") or {}
        if cfg.get("enabled", True) is False:
            return None
        try:
            from .provenance import check as provenance_check
            from .provenance import summarize

            result = provenance_check(
                content,
                getattr(agent, "retrieved_urls", ()) or (),
                verify_unreachable=bool(cfg.get("verify_unreachable", False)),
                max_checks=int(cfg.get("max_checks", 10)),
                timeout=float(cfg.get("timeout", 5.0)),
            )
            line = summarize(result)
            # Quality gate: flag reports whose citations are mostly untraceable.
            min_coverage = cfg.get("min_coverage")
            if min_coverage is not None and result.get("coverage") is not None:
                result["below_threshold"] = result["coverage"] < float(min_coverage)
                if result["below_threshold"]:
                    line += f"（低于阈值 {float(min_coverage):.0%}，请人工复核）"
            if result.get("unmatched"):
                logger.warning("%s | 未匹配示例: %s", line,
                               ", ".join(result["unmatched_examples"][:3]))
                if self._progress:
                    self._progress({"type": "log", "level": "warning",
                                    "message": line})
                    for url in result["unmatched_examples"][:3]:
                        self._progress({"type": "log", "level": "warning",
                                        "message": f"  未在检索结果中的引用: {url}"})
            else:
                logger.info("%s", line)
                if self._progress:
                    self._progress({"type": "log", "level": "info", "message": line})
            return result
        except Exception as exc:  # noqa: BLE001 - provenance must never break a run
            logger.debug("citation check skipped: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Output path resolution
    # ------------------------------------------------------------------

    def _resolve_output_path(self, job: dict) -> str:
        """Determine the output file path from job config.

        Reports may only be written inside the workspace ``output/`` directory
        (or an explicit ``runtime.output_root``). Without this an editor-level
        job YAML could point ``output`` at source files or config.
        """
        raw = job.get("output", "")
        runtime = job.get("runtime") or {}
        configured = (
            runtime.get("output_root")
            or self.sys.get("defaults", {}).get("output_dir", "output")
        )
        allowed_root = (
            os.path.abspath(configured)
            if os.path.isabs(configured)
            else os.path.abspath(os.path.join(self.workspace_dir, configured))
        )
        if not raw:
            name = job.get("name", job.get("_file", "report"))
            ext = ".md"
            raw = os.path.join(configured, f"{{date}}_{name}{ext}")

        path = self._render_path(raw, job)

        # If path is a directory, auto-derive filename from job name
        if path.endswith(os.sep) or (os.path.exists(path) and os.path.isdir(path)):
            name = job.get("name", job.get("_file", "report"))
            safe = re.sub(r'[<>:"/\\|?*]', "_", name)
            path = os.path.join(path, f"{safe}_{self._now():%Y-%m-%d}.md")

        # Resolve relative targets against the workspace (not the process CWD)
        # so validation and the actual write always agree. When an explicit
        # output_root is configured, relative outputs live under that root.
        custom_root = runtime.get("output_root")
        base_dir = allowed_root if custom_root else self.workspace_dir
        was_relative = not os.path.isabs(path)
        resolved = os.path.abspath(os.path.join(base_dir, path))
        if resolved != allowed_root and not resolved.startswith(allowed_root + os.sep):
            raise ValueError(
                f"Output path escapes the allowed root '{allowed_root}': {raw!r}. "
                "Set runtime.output_root explicitly if this is intended."
            )

        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        # Keep relative targets relative so stored paths stay comparable with
        # the report index.
        if was_relative:
            return os.path.relpath(resolved, base_dir if custom_root else self.workspace_dir)
        return resolved

    def _render_path(self, template: str, job: dict) -> str:
        """Substitute {var} placeholders in the output path."""
        name = job.get("name", job.get("_file", ""))
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)
        now = self._now()
        return (
            template.replace("{name}", safe_name)
            .replace("{date}", now.strftime("%Y-%m-%d"))
            .replace("{time}", now.strftime("%H-%M-%S"))
            .replace("{datetime}", now.strftime("%Y-%m-%d_%H-%M-%S"))
        )

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _save_output(self, content: str, path: str) -> str:
        """Write content to file, creating directories as needed.

        If the file already exists, appends a numeric suffix (``_1``, ``_2``,
        etc.) before the extension to avoid overwriting.

        The write is atomic (temp file + rename). A plain ``open(path, "w")``
        truncates before the first byte lands, so a crash or a full disk
        mid-write left a partial report that ``run_job``'s cleanup then treated
        as success -- deleting the stashed previous one.
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

        final_path = path
        counter = 1
        while os.path.exists(final_path):
            root, ext = os.path.splitext(path)
            final_path = f"{root}_{counter}{ext}"
            counter += 1

        atomic_write(final_path, content)
        logger.debug("Saved output to %s", final_path)
        return final_path
