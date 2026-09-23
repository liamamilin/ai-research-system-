# FINAL REPORT

## Acceptance Status

全部验收项通过（详见 ACCEPTANCE.md）：

| 项 | 内容 | 结果 |
|----|------|------|
| Install | pip + npm 安装可复现 | passed |
| Run | `python run.py --list`（24 jobs）、`create_app()` | passed |
| A1 | 行动跟踪（持久化 + 轮次同步 + API） | passed |
| A2 | 跨轮 diff（行动/观察/来源/新域名） | passed |
| A3 | 行动闭环 UI（详情抽屉 + 勾选/放弃/备注） | passed |
| A4 | 报告收藏/标签/已读（迁移 + API + 筛选） | passed |
| A5 | 分享链接（签名 token，未登录只读） | passed |
| A6 | HTML 导出 + 邮件发送报告 | passed |
| A7 | 消费闭环 UI（筛选/星标/分享/导出/公开页） | passed |
| A8 | 轮次摘要推送（Top 行动/测试/观察/失败阶段） | passed |
| A9 | 订阅源（RSS/GitHub/arXiv/HN + job 集成） | passed |
| A10 | 报告评分（存储 + API + Dashboard 聚合） | passed |
| A11 | 语义问答（embedding + 混合检索 + 引用） | passed |
| A12 | 问答 UI（提问 + 引用跳转 + 重建索引） | passed |
| A13 | CI workflow + state 备份 + 首启提示 | passed |
| Error Handling | 新 API 统一 ApiError（401/403/404/422） | passed |
| Tests | 全量门禁 `bash scripts/check.sh` | passed |
| Documentation | README/ROADMAP 同步 | passed |

## Completed Features

**M1 行动闭环**
- `core/tracking.py`：SQLite 跟踪库，内容哈希稳定 ID，跨轮 `carry_over` 分类
- `/api/pipeline/actions`（列表/更新）+ 轮次导出自动同步
- `core/artifacts.diff_rounds` + `/api/pipeline/rounds/{date}/diff`
- Rounds 详情抽屉：行动/观察/diff 三 tab，勾选完成/放弃/备注

**M2 消费闭环**
- reports 表新增 favorite/tags/read_at（旧库自动迁移），`/api/reports/meta`、`/tags`、筛选
- 签名分享链接 `/api/reports/share` + 公开只读 `/api/share/{token}` + `/share/:token` 页面
- HTML 导出（markdown 渲染 + 打印样式）、邮件发送报告（复用 SMTP 渠道）
- Reports 星标/标签/已读筛选，ReportView 工具栏，Dashboard 平均分

**M3 内容推送**
- `core/digest.py`：Top 行动（按优先级）、测试数、观察数、来源数、失败阶段
- 轮次完成通知默认携带摘要（`notifications.digest`，可关闭/限制条数）

**M4 数据源与质量**
- `core/sources.py`：RSS/Atom、GitHub Releases、arXiv、HN → SearchResult，去重合并
- Job `sources:` 预抓取注入 prompt；失败静默降级
- 报告 1-5 星评分 + 备注，`/api/usage/ratings` 按 job 聚合

**M5 语义问答**
- `core/embeddings.py`（OpenAI 兼容 /embeddings，缓存+批处理）
- `web/indexer/vectors.py`：段落分块、向量持久化、余弦检索、FTS+向量混合
- `core/qa.py` + `/api/qa`（回答带编号引用；无 embedding 配置时自动 FTS-only）
- `/api/qa/reindex` 增量嵌入 + 问答页（引用可跳转报告）

**M6 工程化**
- GitHub Actions 运行 `scripts/check.sh`；`scripts/backup_state.sh`（sqlite .backup，--dry-run）
- 首启提示：`/api/setup/status` + 登录页引导（创建 admin / 缺 LLM key 提示）

**过程中修复的真实缺陷**
- FTS5 查询未转义：用户输入 `-`、`*` 等导致 500 → `sanitize_fts_query` 逐词加引号
- logout 清 Cookie 失效（CSRF 失败时 Set-Cookie 被丢弃）
- 4 个自服务写接口缺 CSRF 校验；`WEB_SECRET_KEY` 死配置
- 企业微信/飞书 HTTP 200 + 错误码被误报成功 → 响应体错误码解析

## Validation Evidence

- `bash scripts/check.sh`：backend 191 passed，frontend 19 passed，tsc/eslint/vite build 全绿
- 新增测试文件：tracking, tracking_api, artifacts(diff), reports_meta, reports_api, share, render, digest, sources, embeddings, vectors, qa, backup_script, setup_api
- 安装/运行：pip + npm ok；`run.py --list` 24 jobs；`create_app()` ok
- 备份：`scripts/backup_state.sh --dry-run` 正确列出 users.db/reports.db 等
- 端到端冒烟（真实进程，临时配置/端口 8899）：login/reports/meta/fav/tags/ratings/html/actions/rounds/share 全 200；
  匿名分享读取 200；QA 在无 LLM key 时正确返回 502 且引用检索命中；reindex 无 embedding 配置返回 422

## Remaining Blockers

- **通知渠道配置**：PushPlus / Server酱 / 企业微信 / 飞书 代码与测试全部就绪，但真实到达需要你提供凭证（见 TODO.md）
- **语义问答增强**：需在 `config/system.yaml` 配置 `ai.embedding_model` 才启用向量召回；未配置时自动退化为 FTS-only
- **GitHub Actions**：workflow 已就绪，需要推送到远程仓库后才会实际运行

## Run Instructions

```bash
# 安装
pip install -r requirements.txt && cd ui && npm install && cd ..

# 全量检查
bash scripts/check.sh

# CLI
python run.py --list
python run.py practical_ai_intelligence/00_collection_planner

# Web
python run_web.py serve            # http://localhost:8765

# 备份
bash scripts/backup_state.sh
```

## Final Marker

PROJECT_ACCEPTED
