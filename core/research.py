"""
Research agent - replaces the external coding-CLI agent.

Two execution modes:

- ``agent``:    multi-round tool-calling loop. The model calls ``search_web``,
                ``read_file`` and ``list_files`` until it is ready to write
                the final report.
- ``pipeline``: query planning -> batched web search -> single-shot report
                generation. Used as a fallback when the model/endpoint does
                not support tool calling.

``auto`` (default) tries agent mode first and falls back to pipeline mode.

Configured via ``system.yaml``::

    research:
      mode: "auto"                 # auto | agent | pipeline
      max_rounds: 12
      max_searches: 12
      context_char_budget: 120000
"""

from __future__ import annotations

import fnmatch
import glob
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Optional

# Files the agent's read_file / list_files tools must never reach, even though
# they sit inside the workspace. The workspace is the project root, so it also
# holds .env (the JWT signing key), the user database, and the session store.
#
# This matters because the model's inputs are attacker-influenced: search
# results are fed into its context verbatim, so a poisoned page can try to talk
# it into reading these. An editor-level job plus one share link is otherwise
# enough to walk away with the signing key.
_SENSITIVE_NAMES = (
    ".env", ".env.*",
    "*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "id_rsa", "id_ed25519", ".netrc", ".htpasswd",
)
_SENSITIVE_DIRS = (
    ".git", "state", "config", "secrets", ".ssh", ".aws", ".config",
)


# Replies that mean the model did not do the task. Without this check any
# non-empty string became "the report": an apology, a rate-limit notice, or a
# leaked system prompt would be written to output/ and indexed as research.
_REFUSAL_PREFIXES = (
    "i'm sorry", "i am sorry", "sorry,", "i cannot", "i can't", "i can not",
    "i'm unable", "i am unable", "unable to", "as an ai", "i apologize",
    "抱歉", "对不起", "我无法", "我不能", "无法完成", "作为ai",
)
_REFUSAL_MARKERS = (
    "rate limit", "rate_limit", "too many requests", "http 429",
    "invalid api key", "unauthorized", "quota exceeded",
    "context length", "maximum context", "prompt is too long",
    "internal server error", "service unavailable",
    "upstream connect error", "bad gateway", "gateway timeout",
    "502", "503", "504", "traceback (most recent call last)",
)
# A report is expected to have at least one Markdown heading.
_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
# Stops at whitespace, ASCII punctuation and CJK punctuation. Without the CJK
# class a Chinese sentence yielded "https://x.com/a。参考" as one "URL", which
# then never matched anything the provenance check had actually retrieved.
_URL_RE = re.compile(
    r"https?://[^\s<>()\[\]{}\"'`\\^|，。、；：？！…—～«»“”‘’　]+"
)


def report_problem(content: str, tools_used: int = 0,
                   min_chars: int = 400) -> Optional[str]:
    """Why ``content`` cannot be saved as a report, or None if it looks like one.

    Deliberately conservative: it only rejects shapes that are unambiguously not
    a research report, because a false positive costs a whole run.
    """
    text = (content or "").strip()
    if not text:
        return "内容为空"
    lowered = text[:400].lower()
    for prefix in _REFUSAL_PREFIXES:
        if lowered.startswith(prefix):
            return f"以拒绝/致歉语开头（{prefix!r}）"
    for marker in _REFUSAL_MARKERS:
        if marker in lowered:
            return f"看起来是错误信息（含 {marker!r}）"
    if tools_used == 0 and len(text) < min_chars:
        return f"未做任何检索且仅 {len(text)} 字符（不足 {min_chars}）"
    if len(text) < min_chars:
        return f"仅 {len(text)} 字符，不足 {min_chars}"
    if not _HEADING_RE.search(text) and not _URL_RE.search(text):
        return "没有标题也没有任何引用链接"
    return None


def is_sensitive_path(path: str, workspace: str) -> bool:
    """Whether ``path`` holds credentials rather than research material.

    Checked on the resolved real path, so ``output/../.env`` and a symlink into
    ``state/`` are both caught.
    """
    try:
        relative = os.path.relpath(os.path.realpath(path), os.path.realpath(workspace))
    except ValueError:
        return False  # different drive on Windows; the caller already refused
    if relative.startswith(".."):
        return False  # outside the workspace; containment handles that
    parts = relative.split(os.sep)
    for index, part in enumerate(parts):
        if part in _SENSITIVE_DIRS:
            return True
        if index == len(parts) - 1 and any(
            fnmatch.fnmatch(part, pattern) for pattern in _SENSITIVE_NAMES
        ):
            return True
    return False


# Kept as a module-level alias so the tool methods read cleanly.
_is_sensitive_path = is_sensitive_path

from .errors import CancelledError, LLMError, LLMUnsupportedError
from .llm import LLMClient, LLMConfig
from .search import SearchClient, SearchConfig, SearchResult

logger = logging.getLogger(__name__)


@dataclass
class ResearchConfig:
    """Research loop configuration."""

    mode: str = "auto"
    max_rounds: int = 12
    max_searches: int = 12
    max_chars_per_call: int = 40000
    max_file_chars: int = 60000
    context_char_budget: int = 150000
    min_report_chars: int = 400

    @classmethod
    def from_system(cls, sys_config: dict) -> "ResearchConfig":
        cfg = sys_config.get("research", {}) or {}
        return cls(
            mode=str(cfg.get("mode", "auto")).lower(),
            max_rounds=int(cfg.get("max_rounds", 12)),
            max_searches=int(cfg.get("max_searches", 12)),
            max_chars_per_call=int(cfg.get("max_chars_per_call", 40000)),
            max_file_chars=int(cfg.get("max_file_chars", 60000)),
            context_char_budget=int(cfg.get("context_char_budget", 150000)),
            min_report_chars=int(cfg.get("min_report_chars", 400)),
        )


_AGENT_SYSTEM_PROMPT = """\
You are an autonomous research agent producing complete, evidence-based reports.

Today's date: {date}.

Available tools:
- search_web(objective, queries): search the live web. Returns ranked sources \
with excerpts. Call it multiple times with different angles before writing.
- read_file(path): read a file from the project workspace (relative paths).
- list_files(directory, pattern): list files in the workspace.

Research rules:
1. Gather evidence with search_web before writing. Prefer recent, primary sources.
2. Cite the source URL for every factual claim, finding and price.
3. Never invent facts, URLs, dates or prices. Mark anything unverified explicitly.
4. Respect the time window and output language required by the task.
5. When you have enough evidence, write the FINAL REPORT directly as your \
reply (without calling tools), in Markdown, following the task's structure exactly.
6. The final report must be self-contained: no meta commentary, no tool logs, \
no notes about your research process.
"""

_PIPELINE_SYSTEM_PROMPT = """\
You are a senior research analyst. Write the complete final report in Markdown \
based on the task instructions and the provided web research results.

Rules:
- Follow the task's structure and output language exactly.
- Cite the source URL for every factual finding and price.
- Never invent facts or URLs. If the sources do not cover something, say so \
explicitly instead of guessing.
- Output ONLY the report - no preamble, no meta commentary.
"""

_PLANNER_PROMPT = """\
You plan web research. Given the task below, output ONLY a JSON array of 5-8 \
concise search queries (3-6 words each) covering its key information needs. \
Write the queries in the task's language (or English for technical topics). \
No commentary, no code fences.

TASK:
{task}
"""

_FINALIZE_PROMPT = """\
You have completed your research. Do not call any more tools. Write the \
complete FINAL REPORT now in Markdown, following the task structure exactly \
and citing source URLs for every finding.
"""


class ResearchAgent:
    """Runs the research workflow for a single job."""

    def __init__(
        self,
        llm_config: LLMConfig,
        search_config: SearchConfig,
        research_config: Optional[ResearchConfig] = None,
        workspace_dir: str = ".",
        cancel_token: Optional[threading.Event] = None,
        progress_cb=None,
        timeout_seconds: int = 0,
    ):
        self.llm = LLMClient(
            llm_config, cancel_token=cancel_token, progress_cb=progress_cb
        )
        self.search = SearchClient(
            search_config, cancel_token=cancel_token, progress_cb=progress_cb
        )
        self.config = research_config or ResearchConfig()
        self.workspace = os.path.realpath(workspace_dir)
        self._cancel = cancel_token or threading.Event()
        self._progress = progress_cb or (lambda _: None)
        self._search_calls = 0
        self._tools_used = 0
        # Every URL this run actually retrieved, for citation provenance checks.
        self.retrieved_urls: set[str] = set()
        self._timeout_seconds = int(timeout_seconds or 0)
        self._deadline = (
            time.time() + self._timeout_seconds if self._timeout_seconds else 0.0
        )

    def _remember(self, results) -> None:
        """Record retrieved URLs so the report's citations can be verified."""
        for item in results or []:
            url = getattr(item, "url", "") or ""
            if url:
                self.retrieved_urls.add(url)

    def _remember_urls_in(self, text: str) -> None:
        """Count URLs the model *read* as traceable too.

        A synthesis stage legitimately cites URLs it found in the upstream radar
        documents rather than in its own search results. Only counting searches
        made those look like fabrications: one round's P7 was flagged at 0%
        coverage with 124 "unmatched" URLs, all of them real citations inherited
        from P1-P6.
        """
        for url in _URL_RE.findall(text or ""):
            self.retrieved_urls.add(url.rstrip(".,;)]}'\""))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        task_prompt: str,
        objective: str = "",
        fallback_queries: Optional[list[str]] = None,
    ) -> str:
        """Execute the research workflow and return the final report."""
        mode = self.config.mode
        if mode in ("agent", "auto"):
            try:
                return self._run_agent(task_prompt)
            except LLMUnsupportedError as exc:
                if mode == "agent":
                    raise
                logger.warning(
                    "Tool calling unsupported by model/endpoint (%s); "
                    "falling back to pipeline mode",
                    exc,
                )
                self._progress({"type": "phase", "phase": "pipeline_fallback"})
        return self._run_pipeline(
            task_prompt,
            objective=objective,
            fallback_queries=fallback_queries or [],
        )

    def close(self) -> None:
        """Release HTTP resources."""
        self.llm.close()

    def get_usage(self) -> dict:
        """Return LLM/search consumption accumulated by this run."""
        usage = dict(self.llm.usage)
        usage["model"] = self.llm.config.model
        usage["searches"] = self.search.calls
        return usage

    # ------------------------------------------------------------------
    # Agent mode
    # ------------------------------------------------------------------

    def _run_agent(self, task_prompt: str) -> str:
        self._progress({"type": "phase", "phase": "researching"})
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task_prompt},
        ]

        for round_no in range(1, self.config.max_rounds + 1):
            self._check_cancel()
            self._progress(
                {
                    "type": "round",
                    "round": round_no,
                    "max_rounds": self.config.max_rounds,
                }
            )
            logger.info(
                "Research round %d/%d (searches so far: %d)",
                round_no,
                self.config.max_rounds,
                self._search_calls,
            )

            response = self.llm.chat(messages, tools=_tool_definitions())

            if not response.tool_calls:
                content = response.content.strip()
                # A run that stopped on the output token limit produced half a
                # document. Saving that as the day's report is worse than
                # failing, so it is caught here rather than downstream.
                if response.finish_reason == "length":
                    raise LLMError(
                        f"模型输出在 {self.llm.config.max_tokens} tokens 处被截断，"
                        "报告不完整；请提高 ai.max_tokens 或缩小任务范围"
                    )
                # Accept short replies when no research was performed: the
                # model decided the task needs no tools (e.g. sanity tests).
                if self._tools_used == 0 or len(content) >= self.config.min_report_chars:
                    problem = report_problem(content, self._tools_used,
                                             self.config.min_report_chars)
                    if problem:
                        raise LLMError(f"模型输出不是可用报告：{problem}")
                    self._log_usage()
                    return content
                logger.warning(
                    "Model replied without tools but report is too short "
                    "(%d chars); asking for the full report",
                    len(content),
                )
                if content:
                    messages.append(response.assistant_message)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That reply is too short to be the final report. "
                            "Write the complete report now, following the task "
                            "structure exactly."
                        ),
                    }
                )
                continue

            messages.append(response.assistant_message)
            for tool_call in response.tool_calls:
                self._check_cancel()
                self._tools_used += 1
                self._progress({"type": "tool", "tool": tool_call.name})
                logger.info("Tool call: %s", tool_call.name)
                result = self._execute_tool(tool_call.name, tool_call.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )
            self._compact_history(messages)

        logger.info("Research round limit reached; requesting final report")
        messages.append({"role": "user", "content": _FINALIZE_PROMPT})
        response = self.llm.chat(messages, tools=None)
        self._log_usage()
        return response.content.strip()

    def _execute_tool(self, name: str, arguments: dict) -> str:
        try:
            if name == "search_web":
                return self._tool_search_web(arguments)
            if name == "read_file":
                return self._tool_read_file(arguments)
            if name == "list_files":
                return self._tool_list_files(arguments)
            return f"Error: unknown tool '{name}'"
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fed back to the model
            logger.warning("Tool '%s' failed: %s", name, exc)
            return f"Error: {exc}"

    def _tool_search_web(self, arguments: dict) -> str:
        if self._search_calls >= self.config.max_searches:
            return (
                "Search budget exhausted. Write the final report now using "
                "the evidence already gathered."
            )
        queries = arguments.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        objective = arguments.get("objective") or ""
        self._search_calls += 1

        results = self.search.search(queries, objective=objective)
        self._remember(results)
        if not results:
            return (
                "Error: web search returned no results. Try different queries."
            )
        logger.info(
            "Search tool returned %d sources for: %s",
            len(results),
            "; ".join(queries)[:120],
        )
        return self._truncate(
            SearchClient.format_results(results), self.config.max_chars_per_call
        )

    def _tool_read_file(self, arguments: dict) -> str:
        path = str(arguments.get("path") or "").strip()
        if not path:
            return "Error: 'path' is required"
        resolved = self._resolve_workspace_path(path)
        if resolved is None:
            return f"Error: path escapes the workspace: {path}"
        if not os.path.isfile(resolved):
            return f"Error: file not found: {path}"
        try:
            with open(resolved, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError as exc:
            return f"Error: cannot read {path}: {exc}"
        logger.info("Tool read_file: %s (%d chars)", path, len(content))
        self._remember_urls_in(content)
        return self._truncate(
            f"# {path}\n\n{content}", self.config.max_file_chars
        )

    def _tool_list_files(self, arguments: dict) -> str:
        directory = str(arguments.get("directory") or ".").strip() or "."
        pattern = str(arguments.get("pattern") or "**/*.md").strip() or "**/*.md"
        base = self._resolve_workspace_path(directory)
        if base is None:
            return f"Error: path escapes the workspace: {directory}"
        matches = glob.glob(os.path.join(base, pattern), recursive=True)
        files = sorted(
            os.path.relpath(m, self.workspace)
            for m in matches
            if os.path.isfile(m)
        )
        if not files:
            return f"(no files matching {pattern} under {directory})"
        return "\n".join(files[:200])

    # ------------------------------------------------------------------
    # Pipeline mode (fallback)
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        task_prompt: str,
        objective: str = "",
        fallback_queries: Optional[list[str]] = None,
    ) -> str:
        self._progress({"type": "phase", "phase": "researching"})
        self._check_cancel()
        queries = self._plan_queries(task_prompt)
        if not queries:
            queries = list(fallback_queries or [])
        logger.info("Pipeline search queries: %s", "; ".join(queries) or "(none)")

        results: list[SearchResult] = []
        if queries:
            self._search_calls += 1
            results = self.search.search(queries, objective=objective)
            self._remember(results)

        if results:
            sources = self._truncate(
                SearchClient.format_results(results),
                self.config.context_char_budget // 2,
            )
        else:
            sources = (
                "(web search unavailable or returned no results - write the "
                "report from the task instructions and explicitly mark every "
                "claim you could not verify)"
            )

        self._progress({"type": "phase", "phase": "writing_report"})
        self._check_cancel()
        messages = [
            {"role": "system", "content": _PIPELINE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{task_prompt}\n\n"
                    f"=== WEB RESEARCH RESULTS ===\n\n{sources}\n\n"
                    "=== END OF RESEARCH RESULTS ===\n\n"
                    "Write the complete final report now."
                ),
            },
        ]
        response = self.llm.chat(messages, tools=None)
        self._log_usage()
        return response.content.strip()

    def _plan_queries(self, task_prompt: str) -> list[str]:
        try:
            response = self.llm.chat(
                [
                    {
                        "role": "user",
                        "content": _PLANNER_PROMPT.format(
                            task=task_prompt[:4000]
                        ),
                    }
                ],
                tools=None,
                temperature=0.2,
                max_tokens=500,
            )
        except CancelledError:
            raise
        except LLMError as exc:
            logger.warning("Query planning failed: %s", exc)
            return []

        match = re.search(r"\[.*\]", response.content, re.DOTALL)
        if not match:
            logger.warning("Planner did not return a JSON array")
            return []
        try:
            queries = json.loads(match.group(0))
        except json.JSONDecodeError:
            logger.warning("Planner returned malformed JSON")
            return []
        if not isinstance(queries, list):
            return []
        return [str(q) for q in queries if str(q).strip()][:8]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _system_prompt(self) -> str:
        from datetime import datetime

        return _AGENT_SYSTEM_PROMPT.format(
            date=datetime.now().strftime("%Y-%m-%d")
        )

    def _resolve_workspace_path(self, path: str) -> Optional[str]:
        candidate = path if os.path.isabs(path) else os.path.join(
            self.workspace, path
        )
        resolved = os.path.realpath(candidate)
        if resolved != self.workspace and not resolved.startswith(
            self.workspace + os.sep
        ):
            return None
        if _is_sensitive_path(resolved, self.workspace):
            logger.warning("Tool read refused for sensitive path: %s", path)
            return None
        return resolved

    def _compact_history(self, messages: list[dict]) -> None:
        """Shrink old tool results when the conversation grows too large.

        Keeps the most recent exchanges intact and truncates older tool
        outputs, which are the bulk of the transcript.
        """

        def total_chars() -> int:
            return sum(len(str(m.get("content") or "")) for m in messages)

        if total_chars() <= self.config.context_char_budget:
            return

        keep_recent = 6
        for msg in messages[:-keep_recent]:
            if msg.get("role") != "tool":
                continue
            content = str(msg.get("content") or "")
            if len(content) <= 800:
                continue
            msg["content"] = (
                content[:800]
                + "\n\n[... older search results truncated to save context ...]"
            )
            if total_chars() <= self.config.context_char_budget:
                return
        logger.debug(
            "Context compaction did not reach budget (now %d chars)",
            total_chars(),
        )

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise CancelledError("Cancelled during research")
        if self._deadline and time.time() > self._deadline:
            raise TimeoutError(
                f"Research exceeded the job timeout of "
                f"{self._timeout_seconds}s. Increase runtime.timeout_seconds "
                f"in the job YAML or ai.timeout in config/system.yaml."
            )

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "\n\n[... truncated ...]"

    def _log_usage(self) -> None:
        usage = self.llm.usage
        if usage.get("requests"):
            logger.info(
                "LLM usage: %d requests, %d prompt + %d completion tokens",
                usage["requests"],
                usage["prompt_tokens"],
                usage["completion_tokens"],
            )


def _tool_definitions() -> list[dict]:
    """OpenAI-style tool schemas exposed to the model."""
    return [
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": (
                    "Search the live web for recent information. Returns "
                    "ranked sources with excerpts. Use 1-5 concise keyword "
                    "queries (3-6 words each) and call it multiple times "
                    "with different angles before writing the report."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "objective": {
                            "type": "string",
                            "description": (
                                "Natural-language description of the research "
                                "goal driving this search."
                            ),
                        },
                        "queries": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "1-5 concise keyword queries.",
                        },
                    },
                    "required": ["queries"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": (
                    "Read a text or Markdown file from the project workspace. "
                    "Use relative paths, e.g. "
                    "'output/practical_ai_intelligence/2026-06-27/01_report.md'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace-relative file path.",
                        }
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_files",
                "description": "List files in the project workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": "Workspace-relative directory.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern, e.g. '**/*.md'.",
                        },
                    },
                    "required": [],
                },
            },
        },
    ]
