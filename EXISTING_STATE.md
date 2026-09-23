# EXISTING STATE

## Directory Structure
- `core/` CLI 引擎：engine, research, llm, search, config, state, lock, errors, extractor, budget, notify, report_meta, artifacts
- `web/` FastAPI：auth/(db,jwt,password,routes), routes/(jobs,reports,config,users,logs,scheduler,deploy,usage,pipeline), runner/(executor,registry,log_bus,pipeline), indexer/(db,scanner,watcher), services/(scheduler,yaml_io)
- `ui/` React SPA：pages/(Login,Dashboard,JobsList,JobDetail,Rounds,Reports,ReportView,Scheduler,Settings), components/(Layout,Sidebar,YamlEditor,AccountModal,AuthProvider,CronBuilder,ErrorBoundary), api/(client,index,types), lib/(utils,stores)
- `jobs/` 任务 YAML（monitoring/research/practice/analysis/actionable/practical_ai_intelligence/_templates）
- `config/` system.yaml, web.yaml；`scripts/` check.sh, export_round_artifacts.py 等
- `state/` users.db, reports.db, pipeline_rounds.json, tracking 待建
- `tests/` 后端 85 用例；`ui/src/**/*.test.ts` 前端 19 用例

## Entry Files
`run.py` (CLI), `run_web.py` (Web), `web/server.py:create_app`, `ui/src/App.tsx`

## Current Capabilities
- 研究 agent（工具循环 + pipeline 回退）、5 搜索源、预算护栏、锁/状态/元数据、通知 6 渠道
- Web：JWT/RBAC/CSRF/API Token、Jobs 运行与 SSE 日志、报告浏览 + FTS5 搜索、10 阶段轮次看板（补跑/取消/对比）、cron 调度、系统配置、审计、用量/预算
- 轮次产物：action_items/watchlist/sources JSON（自动导出 + 下载 + API 暴露）

## Known Missing Features
1. 行动/观察项无跟踪（不可勾选、不跨轮延续）；跨轮只对比大小/tokens
2. 报告无收藏/标签/已读、无分享链接、无 HTML/邮件导出
3. 通知只推状态，不推内容摘要（且渠道配置待办）
4. 数据源仅 web search，无 RSS/GitHub/arXiv 订阅源
5. 无报告质量评分反馈
6. 无语义检索/跨报告问答
7. 无 CI workflow、无 state 备份自动化、无首启向导

## Existing Behavior
- 修改 YAML 走 ruamel + 原子写 + 备份 + mtime 冲突检测；错误统一 ApiError 格式
- 轮次产物在 `_run_round` 收尾时导出；报告元数据在 state/report_meta.jsonl
- 前端 401 自动刷新一次；写请求带 X-CSRF-Token

## Existing Tests
- 后端：test_auth, test_pipeline_api, test_artifacts, test_notify, test_pipeline, test_scheduler, test_usage_budget_meta, test_llm
- 前端：api/client.test.ts, lib/utils.test.ts
- 门禁：`bash scripts/check.sh`（compile + pytest + validate + tsc/lint/test/build）

## High Risk Areas
- indexer/db.py 表结构变更需兼容旧库（reports.db 已存在）
- pipeline.py 是长事务/线程池核心，改动需回归 test_pipeline + test_pipeline_api
- 前端 AuthProvider 的登录守卫：新增公开分享页需绕过
- requirements 增加依赖需同步 venv 与文档

## Change Constraints
- 保持现有行为与 API 兼容；新增能力优先用新模块/新端点
- 每个任务必须带可运行的验证命令；全量回归以 check.sh 为准
