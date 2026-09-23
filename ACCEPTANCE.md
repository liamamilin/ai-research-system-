# ACCEPTANCE

## Install
- [x] 依赖安装可复现
  - Evidence: pip install -q -r requirements.txt (ok), ui/npm install --silent (ok)
  - Validation: `pip install -r requirements.txt -q && cd ui && npm install --silent`
  - Status: passed

## Run
- [x] CLI 可运行
  - Evidence: `python run.py --list` -> "All jobs (24):"
  - Validation: `python run.py --list | tail -1`
  - Status: passed
- [x] Web 可启动（导入即可，不常驻）
  - Evidence: create_app() -> "app ok"
  - Validation: `python -c "from web.server import create_app; create_app(); print('app ok')"`
  - Status: passed

## Core Features
- [x] A1 行动跟踪：勾选状态持久化、按轮次同步
  - Evidence: tests/test_tracking.py (10) + tests/test_tracking_api.py (8) passed; pipeline export sync covered in tests/test_pipeline_api.py
  - Validation: `python -m pytest tests/test_tracking.py tests/test_tracking_api.py -q`
  - Status: passed
- [x] A2 跨轮 diff：新增/消失行动、watchlist 变化、新增来源
  - Evidence: tests/test_artifacts.py diff cases + tests/test_pipeline_api.py diff endpoint cases passed
  - Validation: `python -m pytest tests/test_artifacts.py tests/test_pipeline_api.py -q`
  - Status: passed
- [x] A3 行动闭环 UI：轮次详情（行动/观察/diff）+ 勾选交互
  - Evidence: RoundDetail.tsx with tabs + done/drop/note; tsc + eslint clean; full check.sh passed
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: passed
- [x] A4 报告收藏/标签/已读：存储 + API + 列表筛选
  - Evidence: test_reports_meta.py (6) + test_reports_api.py (7) passed, migration covered
  - Validation: `python -m pytest tests/test_reports_meta.py tests/test_reports_api.py -q`
  - Status: passed
- [x] A5 分享链接：签名 token，未登录只读
  - Evidence: test_share.py (6) passed incl. tampered/expired/wrong-scope tokens
  - Validation: `python -m pytest tests/test_share.py -q`
  - Status: passed
- [x] A6 导出：HTML 导出 + 邮件发送报告
  - Evidence: test_render.py (8) passed; SMTP payload decoded and asserted
  - Validation: `python -m pytest tests/test_render.py -q`
  - Status: passed
- [x] A7 消费闭环 UI：星标/标签/已读 + 分享/导出入口 + 公开分享页
  - Evidence: Reports filters + ReportView toolbar + ShareView public route; tsc/eslint/vitest clean
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: passed
- [x] A8 内容推送：轮次摘要（Top 行动/新增观察/失败阶段）
  - Evidence: test_digest.py (7) incl. pipeline notify hook with digest and fallback
  - Validation: `python -m pytest tests/test_digest.py tests/test_notify.py -q`
  - Status: passed
- [x] A9 订阅源：RSS / GitHub Releases / arXiv / HN 适配器与 job 集成
  - Evidence: test_sources.py (8) incl. engine prompt injection + failure fallback
  - Validation: `python -m pytest tests/test_sources.py -q`
  - Status: passed
- [x] A10 报告评分：存储 + API + Dashboard 聚合
  - Evidence: rating API tests + /api/usage/ratings summary; ReportView stars + Dashboard average
  - Validation: `python -m pytest tests/test_reports_api.py -q`
  - Status: passed
- [x] A11 语义问答：embedding + 混合检索 + 带引用回答
  - Evidence: test_embeddings.py (6) + test_vectors.py (7) + test_qa.py (10) passed; FTS fallback + reindex covered
  - Validation: `python -m pytest tests/test_embeddings.py tests/test_qa.py -q`
  - Status: passed
- [x] A12 问答 UI：提问页 + 引用卡片跳转
  - Evidence: Ask page with examples, citations linking to reports, reindex button; tsc/eslint/vitest clean
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: passed
- [x] A13 工程化：CI workflow + state 备份 + 首启提示
  - Evidence: ci.yml yaml ok; test_backup_script.py (4) + test_setup_api.py (3) passed; backup dry-run lists files
  - Validation: `python -m pytest tests/test_setup_api.py -q && bash scripts/backup_state.sh --dry-run`
  - Status: passed

## Error Handling
- [x] 新增 API 的错误遵循 ApiError 格式（401/403/404/422）
  - Evidence: share + tracking API error cases passed (14); qa/reports render error codes asserted
  - Validation: `python -m pytest tests/test_share.py tests/test_tracking_api.py -q`
  - Status: passed

## Tests
- [x] 全量门禁通过（后端 + 前端 + lint + build）
  - Evidence: backend 191 passed, frontend 19 passed, tsc/eslint/vite build clean
  - Validation: `bash scripts/check.sh`
  - Status: passed

## Documentation
- [x] README/ROADMAP 与新能力同步
  - Evidence: README feature table updated (行动跟踪/报告消费/评分/订阅源/问答); ROADMAP milestones all delivered
  - Validation: `rg -n "tracking|share|digest|sources|评分|问答" README.md | head -5`
  - Status: passed

## Final Marker

PROJECT_ACCEPTED can appear in FINAL_REPORT.md only after every acceptance item above has `Status: passed` with recorded evidence and validation output.
