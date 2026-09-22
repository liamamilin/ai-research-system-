# AI Research System

Automated AI research report generator + Web UI console.
Define research jobs in YAML, run them via CLI or web dashboard, browse and search generated reports.

Reports are produced by an autonomous research agent that combines an
**LLM API** (any OpenAI-compatible endpoint) with a **web search API**
(Parallel Search, DuckDuckGo fallback). No CLI agents required.

## Setup

```bash
pip install -r requirements.txt
```

Then configure two things in `.env` (copy from `.env.example`):

| Variable | Purpose |
|----------|---------|
| `LLM_API_KEY` | API key for the LLM endpoint in `config/system.yaml` (empty for local Ollama) |
| `PARALLEL_API_KEY` | Parallel Search API key (`search.provider: parallel`) |

For the web UI: Node.js 18+ is required for the frontend build (optional — prebuilt assets are included).

## Configuration

### System config (`config/system.yaml`)

```yaml
ai:                                   # LLM API (OpenAI-compatible)
  model: "deepseek-v4-flash"          # any OpenAI-compatible model id
  base_url: "https://opencode.ai/zen/v1"
  api_key_env: "LLM_API_KEY"
  timeout: 1800                       # per-request ceiling (seconds)
  max_tokens: 32768
  temperature: 0.3

search:                               # Web search API
  provider: "parallel"                # parallel | tavily | brave | serper | duckduckgo
  api_key_env: "PARALLEL_API_KEY"
  api_key_envs:                       # per-provider keys, switch freely
    tavily: "TAVILY_API_KEY"
    brave: "BRAVE_API_KEY"
    serper: "SERPER_API_KEY"
  mode: "advanced"                    # turbo | fast | basic | advanced
  max_results: 10
  max_chars_per_result: 3000

research:                             # Agent loop
  mode: "auto"                        # auto | agent | pipeline
  max_rounds: 12
  max_searches: 12

notifications:                        # optional alerts (any combination)
  enabled: false
  notify_on: ["failed", "cancelled", "round_finished"]   # + success
  webhook_url: ""                     # Slack/Discord compatible JSON POST
  wecom:                              # 企业微信群机器人
    enabled: false
    webhook_url: ""
  feishu:                             # 飞书群机器人
    enabled: false
    webhook_url: ""
  email:                              # SMTP (SMTP_PASSWORD in .env)
    enabled: false
    smtp_host: ""
    smtp_port: 465
    smtp_user: ""
    smtp_password_env: "SMTP_PASSWORD"
    from_addr: ""
    to_addrs: []
    use_ssl: true

logging:
  level: "INFO"
  file: "logs/ai_research.log"

defaults:
  output_dir: "output"
```

Common endpoints:

| Provider | `base_url` | Notes |
|----------|------------|-------|
| opencode zen | `https://opencode.ai/zen/v1` | pay-as-you-go; needs credits |
| Ollama (local) | `http://localhost:11434/v1` | free; `api_key_env: ""` |
| DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` |

### Web UI config (`config/web.yaml`)

```yaml
server:
  host: "127.0.0.1"       # bind 0.0.0.0 behind a reverse proxy in production
  port: 8765

auth:
  secret_key: "CHANGE_ME" # use a long random string in production
  access_token_minutes: 15
  refresh_token_days: 7
  cookie_secure: false     # set true in production (HTTPS)
```

Copy from `config/web.yaml.example` and edit.

## Usage

### CLI

```bash
python run.py                      # Run all enabled jobs
python run.py daily_ai_agents      # Run specific job(s)
python run.py job1 job2            # Run multiple
python run.py --list               # List all jobs
python run.py --list-enabled       # List enabled jobs
python run.py -v job_name          # Verbose mode
python run.py -t 3600 job_name     # Timeout override (seconds)
python run.py --validate           # Validate configs

# Export structured JSON artifacts for intelligence rounds
python scripts/export_round_artifacts.py              # latest round
python scripts/export_round_artifacts.py 2026-09-19   # one round
python scripts/export_round_artifacts.py --all        # every round
```

### macOS 启动器 App

项目根目录有 `AI Research Console.app`（源码：`scripts/launcher.applescript`）：

- 双击：服务未启动 → 自动启动并打开浏览器；服务已启动 → 直接跳转
- 再次点击（App 已在运行）：跳转到服务页面
- 看门狗：每 60s 检查一次 `/api/health`，服务连续 30 分钟无响应则自动关闭 App
- 退出 App 不会停止服务（服务以 nohup 分离运行）；下次点击会重新接管
- 服务输出写入 `logs/app_launcher.log`

重新编译：`osacompile -o "AI Research Console.app" scripts/launcher.applescript`

### Web UI

```bash
# Create the first admin user (one-time)
python run_web.py create-admin <username> <password>

# Start the server
python run_web.py serve                    # http://localhost:8765

# Dev mode (with hot-reload)
python run_web.py serve --reload

# Other commands
python run_web.py create-user <user> <pwd> --role editor
python run_web.py reindex                  # Force re-scan output/ for search index
```

#### Features

| Feature | Description |
|---------|-------------|
| Dashboard | Job statistics, recent runs, token usage & estimated cost, 月度预算进度 |
| Rounds board | 情报轮次看板：10 阶段状态、一键运行、失败阶段补跑、历史轮次；每轮自动生成并在卡片上直接下载 `action_items.json` / `watchlist.json` / `sources.json` 结构化产物（失败可手动补跑导出脚本） |
| Account | 修改密码、会话管理、API Token（脚本/CI 用 Bearer 调用，免 CSRF）；管理员可改角色/禁用/重置密码/强制下线 |
| Notifications | 通知（失败/取消/完成/轮次完成）：Webhook（Slack/Discord 兼容）、企业微信、飞书、邮件 |
| Job management | Browse jobs, view/edit YAML config (Monaco editor), trigger/cancel runs |
| Real-time logs | SSE stream of job execution logs with live status updates |
| Report viewer | Markdown rendering (GFM tables, code blocks, TOC sidebar), 报告元数据（模型/tokens/耗时）|
| Live generation | 流式生成进度（thinking/writing 字数、耗时），工具轮带心跳 |
| Full-text search | FTS5-powered search across report titles and content |
| User management | Admin-panel: create/delete users with role-based access |
| Audit log | Track all mutations (run, cancel, YAML edit, config change) |
| System config | Edit system.yaml from the browser (admin only) |

## Job Files

Jobs live in `jobs/*.yaml`, organized by category. Copy a template from `jobs/_templates/` to get started:

```yaml
name: "Daily AI Agents"
description: "Daily AI agents news"
enabled: true
keywords:
  - AI agents
  - autonomous agents
language: "zh"
prompt: |
  Research {name}. Keywords: {keywords}. Language: {language}.
  Generate a report...
output: "output/daily/{date}_daily_ai_agents.md"
```

### Template variables

| Variable | Example |
|----------|---------|
| `{name}` | Daily AI Agents |
| `{keywords}` | AI agents, autonomous agents |
| `{language}` | zh |
| `{date}` | 2026-05-22 |
| `{time}` | 14-30-00 |
| `{datetime}` | 2026-05-22_14-30-00 |

## Directory Structure

```
├── run.py                  # CLI entry (argparse)
├── run_web.py              # Web UI entry (FastAPI + Uvicorn)
│
├── core/                   # CLI engine (shared with web)
│   ├── engine.py           # ResearchEngine: load → render → research → save
│   ├── research.py         # ResearchAgent: tool loop + pipeline fallback
│   ├── llm.py              # LLM API client (OpenAI-compatible)
│   ├── search.py           # Search API client (Parallel / DuckDuckGo)
│   ├── config.py           # YAML config loading
│   ├── state.py            # Job run state + history tracking
│   ├── lock.py             # File-based lock (prevents concurrent runs)
│   ├── errors.py           # Shared exception types
│   ├── extractor.py        # Optional report cleanup pass
│   └── artifacts.py        # Round JSON artifacts (actions/watchlist/sources)
│
├── web/                    # Web backend (FastAPI)
│   ├── server.py           # App entry, CORS, lifespan, SPA serving
│   ├── settings.py         # web.yaml config loader
│   ├── auth/               # JWT auth, bcrypt, user DB, sessions
│   ├── routes/             # API routes: jobs, reports, config, users, logs
│   ├── runner/             # Async job executor, SSE log bus, task registry
│   ├── indexer/            # FTS5 report indexer, scanner, file watcher
│   └── services/           # YAML I/O (ruamel, backup, conflict detection)
│
├── ui/                     # Frontend (React + Vite + TypeScript)
│   ├── src/pages/          # Login, Dashboard, JobsList, JobDetail,
│   │                       # Reports, ReportView, Settings
│   ├── src/components/     # Layout, Sidebar, YamlEditor, AuthProvider
│   └── src/hooks/          # useLogStream (SSE), auth store, etc.
│
├── jobs/                   # Job definitions by category
│   ├── simple_test.yaml
│   ├── monitoring/         # Daily monitoring jobs
│   ├── research/           # Survey / deep research
│   ├── practice/           # Practical applications
│   ├── analysis/           # Trend / gap analysis
│   ├── actionable/         # Research-to-action plans
│   └── _templates/         # Reusable job templates
│
├── config/
│   ├── system.yaml         # AI model, timeout, logging
│   └── web.yaml            # Server host, port, JWT secret, CORS
│
├── output/                 # Generated reports (indexed by FTS5)
│   └── practical_ai_intelligence/<date>/   # 10 docs + action_items/watchlist/sources JSON
├── state/                  # Job state, history, user DB, reports index
├── logs/                   # App log, audit log, job-run logs
└── utils/                  # Logger setup
```

## Architecture

### CLI Mode

```
run.py → ResearchEngine
              ├── config.py (load job YAML)
              ├── _build_prompt()  ({var} substitution)
              ├── ResearchAgent.run()
              │     ├── LLMClient.chat()      (OpenAI-compatible API)
              │     ├── SearchClient.search() (Parallel / DuckDuckGo)
              │     └── tools: search_web, read_file, list_files
              └── _save_output()   (writes the final Markdown report)
```

### Web UI Mode

```
Browser (React SPA)
  │ HTTP/JSON + SSE
  ▼
FastAPI server
  ├── Auth (JWT + httpOnly cookies + CSRF)
  ├── Jobs API (list, edit, run/cancel via SSE, history)
  ├── Reports API (browse, search via FTS5)
  ├── Config API (system.yaml, user management, audit)
  └── Job Runner (ThreadPool + TaskRegistry + LogBus)
        │
        ▼
      core/engine.py (same engine, cancel_token + progress_cb hooks)
```

### Key Design Decisions

- **Single YAML job file** = keywords + prompt template + output path, all inline.
- **Research agent**: multi-round tool-calling loop (`search_web`, `read_file`, `list_files`). The model searches the web, reads prior reports from the workspace (needed by synthesis jobs), then writes the final report as its reply. If the endpoint does not support tool calling, it automatically falls back to pipeline mode (plan queries → search → single-shot generation).
- **Search API**: Parallel Search returns ranked URLs with LLM-ready markdown excerpts; DuckDuckGo is an optional keyless fallback.
- **Direct LLM API**: any OpenAI-compatible `/chat/completions` endpoint; retries with backoff, cancellable in-flight requests, token usage logged.
- **Progress visibility**: CLI prints stage-by-stage messages; Web UI streams logs via SSE with real-time status.
- **Cancel safety**: `threading.Event` propagated through the engine → aborts in-flight LLM requests and search calls → state marked `cancelled`.
- **YAML safety**: `ruamel.yaml` preserves comments/formatting; atomic write (`.tmp` + `rename`); automatic backup on every edit; mtime-based conflict detection.
- **Report search**: SQLite FTS5 indexes all `.md` files in `output/`; incremental `watchfiles` watcher; results include highlighted snippets.
- **Optional cleanup**: a cheap model can post-process the report (`extraction.enabled: true`) — disabled by default since API output is already clean.

## Scheduling (CLI)

Use system cron:

```bash
# Daily 8 AM
0 8 * * * cd /path/to/project && python run.py daily_ai_agents
```

## Deployment

### Quick start (Docker)

```bash
# 1. Set secrets
cp .env.example .env
# Edit .env with your AI_RESEARCH_SECRET_KEY and EXTRACTION_API_KEY

# 2. Build & start
docker compose up -d
# Visit http://localhost:8765

# 3. Create first admin
docker compose exec ai-research python run_web.py create-admin admin your-password

# 4. View logs
docker compose logs -f
```

### Manual deploy

```bash
# Build everything
bash scripts/build.sh production

# Deploy to target directory
bash scripts/deploy.sh /opt/ai-research
```

### Systemd service (FastAPI)

```
[Unit]
Description=AI Research Console
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/opt/ai-research
ExecStart=/opt/ai-research/venv/bin/python run_web.py serve
Restart=on-failure
RestartSec=10
Environment=AI_RESEARCH_SECRET_KEY=your-secret-key
Environment=AI_RESEARCH_ENV=production
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

Pre-built config file: `deploy/ai-research.service`

### Nginx reverse proxy

```nginx
server {
    listen 443 ssl http2;
    server_name research.example.com;
    # ... SSL config ...
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_buffering off;  # Required for SSE
        proxy_read_timeout 86400;
    }
    location /assets/ {
        proxy_pass http://127.0.0.1:8765;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

Pre-built config file: `deploy/nginx.conf`

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AI_RESEARCH_ENV` | No | `development` | `development`, `staging`, or `production` |
| `AI_RESEARCH_SECRET_KEY` | Production | `CHANGE_ME` | JWT signing key (generate: `openssl rand -hex 32`) |
| `AI_RESEARCH_WEB_CONFIG` | No | `config/web.yaml` | Override web config path |
| `EXTRACTION_API_KEY` | No | — | Report extraction API key (referenced in `system.yaml`) |
| `LLM_API_KEY` | Hosted LLM endpoints | — | LLM API key (referenced by `ai.api_key_env`) |
| `PARALLEL_API_KEY` | `search.provider: parallel` | — | Parallel Search API key (referenced by `search.api_key_env`) |

### Security notes

- **Change the JWT secret**: Set `AI_RESEARCH_SECRET_KEY` environment variable or update `config/web.yaml`.
- **HTTPS only**: Set `cookie_secure: true` in production.
- **Bind to localhost**: Behind a reverse proxy, bind to `127.0.0.1`. For direct access, bind `0.0.0.0` with proper firewall.
- **Rate limiting**: Login is rate-limited at 5 attempts/minute/IP. Job runs at 10/minute/user.
- **Audit trail**: All mutations are logged to `logs/audit.jsonl`.

## Development

```bash
# Backend (hot-reload)
python run_web.py serve --reload

# Frontend (Vite dev server, proxied to backend)
cd ui && npm run dev

# Local CI: compile + unit tests + config validation + frontend build
bash scripts/check.sh

# Unit tests only
pip install -r requirements-dev.txt
python -m pytest -q
```

### Budget guardrail

`config/system.yaml` 的 `budget.monthly_usd_limit` > 0 时：

- Dashboard 显示月度预算进度条，超 `warn_ratio` 变色
- 超限后 Web「运行轮次/补跑」被拒绝（可配 `block_pipeline: false` 关闭）
- cron 流水线在开跑前会先执行 `python run.py --check-budget`，超限则跳过
  （`SKIP_BUDGET_CHECK=1` 可强制运行）

## API Overview

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/login` | public | Login, get JWT cookies |
| POST | `/api/auth/refresh` | cookie | Refresh access token |
| GET | `/api/jobs` | viewer+ | List all jobs |
| POST | `/api/jobs/{name}/run` | editor+ | Trigger job run |
| POST | `/api/jobs/{name}/cancel` | editor+ | Cancel running job |
| GET | `/api/jobs/{name}/stream` | viewer+ | SSE log stream |
| PUT | `/api/jobs/{name}` | editor+ | Edit job YAML |
| GET | `/api/reports` | viewer+ | List reports (paginated) |
| GET | `/api/reports/search?q=...` | viewer+ | FTS5 full-text search |
| GET | `/api/reports/raw` | viewer+ | Read report markdown |
| GET | `/api/config/system` | admin | Read system config |
| PUT | `/api/config/system` | admin | Update system config |
| GET | `/api/users` | admin | List users |
| POST | `/api/users` | admin | Create user |
| GET | `/api/audit` | admin | Audit log |
| GET/POST | `/api/auth/tokens` | viewer+ | List / create API tokens |
| DELETE | `/api/auth/tokens/{id}` | viewer+ | Revoke an API token |
| GET | `/api/usage` | viewer+ | Token usage + estimated cost + budget |
| GET/POST | `/api/pipeline/rounds`, `/run`, `/retry`, `/cancel` | viewer+/editor+ | 情报轮次 |
