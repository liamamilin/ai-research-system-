# TASKS

- [x] T101: 行动跟踪存储 core/tracking.py
  - Goal: SQLite 存储行动/测试/观察项状态，内容哈希稳定 ID，支持同步与状态更新
  - Files: core/tracking.py, tests/test_tracking.py
  - Validation: `python -m pytest tests/test_tracking.py -q`
  - Status: done
  - Blocked By:

- [x] T102: 跟踪 API（列表/更新）
  - Goal: GET /api/pipeline/actions、PATCH /api/pipeline/actions/{id}（editor+，CSRF）
  - Files: web/routes/tracking.py, web/server.py, tests/test_tracking_api.py
  - Validation: `python -m pytest tests/test_tracking_api.py -q`
  - Status: done
  - Blocked By:

- [x] T103: 轮次导出时同步跟踪库
  - Goal: 轮次产物导出后自动 upsert 行动/观察项，记录 last_seen
  - Files: web/runner/pipeline.py, core/tracking.py, tests/test_tracking.py
  - Validation: `python -m pytest tests/test_tracking.py tests/test_pipeline_api.py -q`
  - Status: done
  - Blocked By:

- [x] T104: 跨轮 diff 引擎
  - Goal: diff_rounds 输出新增/消失行动、watchlist 变化、新增来源与域名
  - Files: core/artifacts.py, tests/test_artifacts.py
  - Validation: `python -m pytest tests/test_artifacts.py -q`
  - Status: done
  - Blocked By:

- [x] T105: diff API
  - Goal: GET /api/pipeline/rounds/{date}/diff?against=YYYY-MM-DD
  - Files: web/routes/pipeline.py, tests/test_pipeline_api.py
  - Validation: `python -m pytest tests/test_pipeline_api.py -q`
  - Status: done
  - Blocked By: T104

- [x] T106: 轮次详情抽屉（行动/观察/diff）
  - Goal: Rounds 页新增详情抽屉组件与 API 封装
  - Files: ui/src/components/RoundDetail.tsx, ui/src/pages/Rounds.tsx, ui/src/api/index.ts
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: done
  - Blocked By: T102, T105

- [x] T107: 行动勾选交互
  - Goal: 详情抽屉内勾选完成/放弃 + 备注，乐观更新
  - Files: ui/src/components/RoundDetail.tsx, ui/src/api/index.ts
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: done
  - Blocked By: T106

- [x] T201: 报告元数据列（收藏/标签/已读）
  - Goal: reports 表新增列并兼容旧库迁移，支持筛选
  - Files: web/indexer/db.py, tests/test_reports_meta.py
  - Validation: `python -m pytest tests/test_reports_meta.py -q`
  - Status: done
  - Blocked By:

- [x] T202: 报告元数据 API
  - Goal: PATCH /api/reports/meta、列表 favorite/tag 筛选
  - Files: web/routes/reports.py, tests/test_reports_api.py
  - Validation: `python -m pytest tests/test_reports_api.py -q`
  - Status: done
  - Blocked By: T201

- [x] T203: 分享链接（签名 token + 公开只读端点）
  - Goal: 生成带 scope/exp 的 token；GET /api/share/{token} 未登录可读
  - Files: core/share.py, web/routes/share.py, web/server.py, tests/test_share.py
  - Validation: `python -m pytest tests/test_share.py -q`
  - Status: done
  - Blocked By:

- [x] T204: HTML 导出 + 邮件发送报告
  - Goal: markdown→HTML 渲染端点与 notify 邮件复用
  - Files: core/render.py, requirements.txt, web/routes/reports.py, tests/test_render.py
  - Validation: `python -m pytest tests/test_render.py -q`
  - Status: done
  - Blocked By:

- [x] T205: 消费闭环 UI（星标/标签/已读/分享/导出/公开页）
  - Goal: Reports 列表与 ReportView 工具栏 + 公开分享页
  - Files: ui/src/pages/Reports.tsx, ui/src/pages/ReportView.tsx, ui/src/pages/ShareView.tsx, ui/src/App.tsx, ui/src/api/index.ts
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: done
  - Blocked By: T202, T203, T204

- [x] T301: 轮次摘要构建 core/digest.py
  - Goal: 从 artifacts 生成 Top 行动/新增观察/失败阶段摘要
  - Files: core/digest.py, tests/test_digest.py
  - Validation: `python -m pytest tests/test_digest.py -q`
  - Status: done
  - Blocked By:

- [x] T302: 摘要接入轮次通知与配置
  - Goal: round_finished 附带 digest；notifications.digest 配置
  - Files: core/notify.py, web/runner/pipeline.py, config/system.yaml, tests/test_digest.py
  - Validation: `python -m pytest tests/test_digest.py tests/test_notify.py -q`
  - Status: done
  - Blocked By: T301

- [x] T401: 订阅源适配器
  - Goal: RSS/Atom、GitHub Releases、arXiv、HN → SearchResult
  - Files: core/sources.py, tests/test_sources.py
  - Validation: `python -m pytest tests/test_sources.py -q`
  - Status: done
  - Blocked By:

- [x] T402: job sources 集成
  - Goal: Job YAML 支持 sources:，与搜索合并去重
  - Files: core/config.py, core/research.py, jobs/_templates/_daily.yaml, tests/test_sources.py
  - Validation: `python -m pytest tests/test_sources.py tests/test_pipeline.py -q`
  - Status: done
  - Blocked By: T401

- [x] T403: 报告评分 API
  - Goal: rating(1-5)+note 存储；usage 聚合平均分
  - Files: web/indexer/db.py, web/routes/reports.py, web/routes/usage.py, tests/test_reports_api.py
  - Validation: `python -m pytest tests/test_reports_api.py -q`
  - Status: done
  - Blocked By: T201

- [x] T404: 评分 UI 与 Dashboard 聚合
  - Goal: ReportView 评分控件；Dashboard 平均分卡片
  - Files: ui/src/pages/ReportView.tsx, ui/src/pages/Dashboard.tsx, ui/src/api/index.ts
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: done
  - Blocked By: T403

- [x] T501: embedding 客户端
  - Goal: OpenAI 兼容 /embeddings 调用，缓存与错误处理
  - Files: core/embeddings.py, tests/test_embeddings.py
  - Validation: `python -m pytest tests/test_embeddings.py -q`
  - Status: done
  - Blocked By:

- [x] T502: 分块 + 向量存储 + 混合检索
  - Goal: 报告分块、向量持久化、FTS+向量召回
  - Files: web/indexer/vectors.py, web/indexer/db.py, tests/test_vectors.py
  - Validation: `python -m pytest tests/test_vectors.py -q`
  - Status: done
  - Blocked By: T501

- [x] T503: 问答 API（带引用）
  - Goal: POST /api/qa 检索+生成，返回引用列表
  - Files: web/routes/qa.py, web/server.py, core/qa.py, tests/test_qa.py
  - Validation: `python -m pytest tests/test_qa.py -q`
  - Status: done
  - Blocked By: T502

- [x] T504: 问答 UI
  - Goal: 新增问答页与引用卡片跳转
  - Files: ui/src/pages/Ask.tsx, ui/src/App.tsx, ui/src/components/Sidebar.tsx, ui/src/api/index.ts
  - Validation: `cd ui && npx tsc --noEmit && npm test --silent`
  - Status: done
  - Blocked By: T503

- [x] T601: CI workflow
  - Goal: GitHub Actions 运行 scripts/check.sh
  - Files: .github/workflows/ci.yml
  - Validation: `python -c "import yaml,pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text()); print('yaml ok')"`
  - Status: done
  - Blocked By:

- [x] T602: state 备份脚本
  - Goal: 备份 users.db/reports.db/tracking.db/报告元数据，支持 --dry-run
  - Files: scripts/backup_state.sh, tests/test_backup_script.py
  - Validation: `bash scripts/backup_state.sh --dry-run && python -m pytest tests/test_backup_script.py -q`
  - Status: done
  - Blocked By:

- [x] T603: 首启提示
  - Goal: 无用户时公开 setup 状态 + 登录页引导
  - Files: web/routes/setup.py, web/server.py, ui/src/pages/Login.tsx, ui/src/api/index.ts, tests/test_setup_api.py
  - Validation: `python -m pytest tests/test_setup_api.py -q`
  - Status: done
  - Blocked By:
