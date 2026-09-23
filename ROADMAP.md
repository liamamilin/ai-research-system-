# 开发计划（Roadmap）

目标：把系统从「能生成报告」推进到「**可消费、可行动、可迭代**」——报告产出后有人看、行动有跟踪、判断有对比、质量有反馈。

## 基线（已完成）

- CLI 引擎：研究 agent（工具循环 + pipeline 回退）、5 个搜索源、预算护栏、锁/状态/报告元数据
- Web 控制台：JWT/RBAC/CSRF/API Token、Jobs、报告浏览 + FTS5 检索、10 阶段轮次看板（补跑/取消/对比）、cron 调度、系统配置、审计日志、用量与预算
- 轮次结构化产物：`action_items.json` / `watchlist.json` / `sources.json`（自动导出 + UI 下载）
- 通知：webhook / 企业微信 / 飞书（含签名）/ PushPlus / Server酱 / 邮件（配置待办，见 TODO.md）
- 质量：后端 85 测试 + 前端 19 测试 + ESLint，`scripts/check.sh` 一键 CI

## M1 行动闭环（优先，纯本地数据）

**交付物**
- 行动/测试/观察项跟踪：勾选完成、放弃、备注；状态持久化（`state/tracking.db`，按条目文本哈希生成稳定 ID）
- 跨轮延续视图：某条行动在后续轮次是否再次出现、上次状态；watchlist 触发信号标记「已触发」
- 内容级跨轮 diff：新行动 / 消失的行动 / 新增来源与域名 / watchlist 变化（升级现有「对比上一轮」）

**技术**
- `core/tracking.py` + `core/artifacts.py:diff_rounds()`
- API：`GET/PATCH /api/pipeline/actions`、`GET /api/pipeline/rounds/{date}/diff`
- UI：Rounds 详情抽屉（行动 / 观察 / diff 三个 tab）

**验收**：勾选状态重启不丢；diff 能列出新增行动与新增来源；新增测试覆盖 tracking 与 diff。

**工期**：2–3 天

## M2 消费闭环

**交付物**
- 报告收藏（星标）、标签、已读状态；列表按收藏/标签筛选
- 分享链接：带 scope 的签名 token，未登录只读查看（不暴露其他数据）
- 导出：Markdown / HTML（浏览器打印为 PDF）；邮件发送报告（复用 notify 邮件渠道）

**技术**
- `web/indexer/db.py` 增加 `favorite / tags / read_at` 列（迁移兼容旧库）
- `PATCH /api/reports/meta`、`GET /api/share/{token}`、`POST /api/reports/{path}/email`
- UI：Reports 列表 + ReportView 工具栏

**验收**：收藏/标签/已读重启不丢；分享链接在无 cookie 浏览器可打开且只读；导出 HTML 保留表格与代码块。

**工期**：3–4 天

## M3 内容级推送（依赖通知渠道配置）

**交付物**
- 轮次摘要推送：Top N 行动（按优先级）+ 新增观察项 + 失败阶段；替代当前只有状态的通知
- 报告完成可选推送（标题 + 关键结论 + 链接）

**技术**
- `core/digest.py` 从 artifacts 生成摘要；`notifications.digest` 配置（开关/条数/是否含 watchlist）
- 事件扩展：`round_finished` 附带 digest，或新增 `digest` 事件

**验收**：`--test-notify` 输出摘要样例；真实轮次结束后群/微信收到含行动清单的消息。

**工期**：1 天（通知配置完成前保持 TODO 状态）

## M4 数据源扩展与质量反馈

**交付物**
- 订阅源适配器：RSS/Atom、GitHub Releases（公开仓库免 key）、arXiv、Hacker News
- Job YAML 支持 `sources:` 与 web search 混用、去重合并
- 报告评分（1–5）+ 备注；Dashboard 展示按 job/模型的平均评分

**技术**
- `core/sources/`（统一 `fetch() -> list[SearchResult]` 接口，复用现有格式化与去重）
- 评分写入报告元数据；`GET /api/usage` 聚合评分

**验收**：不配置搜索 API 也能靠 RSS+GitHub 完成一次 job；评分持久化并在 Dashboard 可见。

**工期**：3–4 天

## M5 语义检索与问答

**交付物**
- 混合检索：FTS5 + 向量召回 + 重排
- 跨报告问答：回答带引用（链接到具体报告），成本计入用量统计
- 索引增量更新（复用现有 watcher）

**技术**
- Embedding 走 OpenAI 兼容端点（`ai.embedding_model`），向量存 SQLite（sqlite-vec 或 numpy 持久化）
- 分块策略 + 缓存（相同内容不重复 embedding）；`POST /api/qa`
- UI：新增「问答」页（引用卡片 + 跳转报告）

**验收**：跨 ≥3 份报告提问，答案引用可点击且相关；索引增量更新不重复计费。

**工期**：5–7 天（本计划中最大项，可延后）

## M6 工程化收尾

- GitHub Actions 接入 `scripts/check.sh`（需远程仓库）
- `state/` 数据库与报告元数据定期备份脚本 + cron 示例
- 首启向导：UI 内创建 admin、配置 LLM/搜索 key、一键测试连接

**工期**：1–2 天

## 排期总览

| 里程碑 | 依赖 | 工期 | 建议顺序 |
|--------|------|------|----------|
| M1 行动闭环 | 无 | 2–3 天 | 1 |
| M2 消费闭环 | 无 | 3–4 天 | 2 |
| M3 内容推送 | 通知渠道配置（TODO.md） | 1 天 | 通知配好后立即 |
| M4 数据源 + 质量 | 无 | 3–4 天 | 3 |
| M5 语义问答 | 无（建议 M2 后） | 5–7 天 | 4 |
| M6 工程化 | 远程仓库 | 1–2 天 | 穿插 |

## 暂缓 / 不做

- 多租户、对外 SaaS、计费：与当前单用户定位不符
- 移动 App：通知 + 响应式 Web 已覆盖主要场景
- 自建 benchmark 评测管线：等 M4 质量反馈积累数据后再评估
