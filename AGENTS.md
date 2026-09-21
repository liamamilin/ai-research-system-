# AI Research System

Automated AI research report generation via LLM API + web search API + Web UI console.
Single YAML job file = everything.

## Quick Start

```bash
pip install -r requirements.txt   # Python dependencies
cp .env.example .env              # then set LLM_API_KEY / PARALLEL_API_KEY
python run.py --list              # See available jobs
python run.py daily_ai_agents     # Run one
python run.py --validate          # Check configs

# Web UI
python run_web.py create-admin <username> <password>
python run_web.py serve           # http://localhost:8765
```

## How It Works (CLI)

```
Job YAML → render prompt → ResearchAgent → final Markdown report
                              ├── LLMClient.chat()       (OpenAI-compatible API)
                              ├── SearchClient.search()  (Parallel / DuckDuckGo)
                              └── tools: search_web, read_file, list_files
```

The research agent runs a multi-round tool-calling loop: the model searches the
web (often several times with different angles), reads prior reports from the
workspace when the task references them (e.g. synthesis stages), then writes
the complete report as its final reply. The engine saves that reply to the
job's `output` path — no CLI agent, no guard instructions.

When the endpoint/model does not support tool calling, `research.mode: auto`
falls back to pipeline mode: plan queries → batched search → single-shot
generation.

## Configuration

| File | Contents |
|------|----------|
| `config/system.yaml` | `ai` (LLM endpoint), `search` (provider + limits), `research` (loop limits), optional `extraction` cleanup |
| `.env` | `LLM_API_KEY`, `PARALLEL_API_KEY` (env vars referenced by system.yaml) |
| `jobs/*.yaml` | Per-job prompt, keywords, output path, `runtime.timeout_seconds` |

The LLM endpoint must be OpenAI-compatible (`/chat/completions`). Works with
opencode zen, OpenAI, DeepSeek, local Ollama (`http://localhost:11434/v1`,
empty `api_key_env`), etc.

## How It Works (Web UI)

```
React SPA → FastAPI → core/engine.py (with cancel_token + progress_cb)
  SSE stream → real-time log display
  FTS5 index → full-text report search
  JWT auth → role-based access (viewer / editor / admin)
```

## Conventions

- **Python 3.10+**, type annotations, docstrings on public methods
- **Logging** via `utils/logger.py` → setup_logger()
- **Error handling**: catch, log, return None (never crash)
- **Config**: YAML only, no Python config files
- **LLM access**: OpenAI-compatible HTTP API only — never shell out to CLI agents
- **Frontend**: React 18 + Vite + TypeScript + TailwindCSS

## Scheduling

Use cron:

```bash
0 8 * * * cd /path/to/project && python run.py daily_ai_agents
```

## Adding a Job

Create a YAML file in the appropriate `jobs/` subdirectory (or copy from `jobs/_templates/`):

```yaml
name: "My Research"
description: "My custom research"
enabled: true
keywords: [topic1, topic2]
language: "zh"
runtime:
  timeout_seconds: 3600
prompt: |
  Research {name} with keywords: {keywords}.
  Generate a report in Markdown. Cite source URLs for every finding.
output: "output/my_job_{date}.md"
```

### Job categories

| Directory | Purpose | Example |
|-----------|---------|---------|
| `monitoring/` | Daily monitoring, quick checks | `daily_ai_agents` |
| `research/` | Deep surveys, literature reviews | `ai_survey` |
| `practice/` | Real-world applications, case studies | `ai_agents_practice` |
| `analysis/` | Trend analysis, gap/opportunity detection | `ai_trends` |
| `actionable/` | Research-to-action plans | `ai_agents_actionable` |
| `_templates/` | Reusable job templates (ignored by engine) | `_daily.yaml` |

Jobs can be referenced by basename (`daily_ai_agents`) or full path (`monitoring/daily_ai_agents`).

## Web UI Roles

| Role | Permissions |
|------|------------|
| `viewer` | Browse jobs, reports, logs. Read-only. |
| `editor` | Viewer + trigger/cancel runs, edit job YAML |
| `admin` | Editor + system config, user management, audit log |
