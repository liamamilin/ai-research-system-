# TODO

完整开发计划见 [ROADMAP.md](ROADMAP.md)（M1 行动闭环 → M2 消费闭环 → M3 内容推送 → M4 数据源/质量 → M5 语义问答 → M6 工程化）。

## 信息推送（通知渠道）验证

状态：待做（代码已完成，只差配置与实测）

- [ ] 二选一取凭证：PushPlus（pushplus.plus 微信扫码 → token）或 Server酱（sct.ftqq.com → SendKey）
- [ ] 写入 `.env`：`PUSHPLUS_TOKEN` 或 `SERVERCHAN_SENDKEY`
- [ ] `config/system.yaml`：`notifications.enabled: true` + 对应渠道 `enabled: true`
- [ ] 运行 `python run.py --test-notify`，确认微信收到「通知测试」
- [ ] 实测一次失败/轮次完成事件（如 `simple_test` 失败路径或下一轮 pipeline 结束）

备选渠道（均已支持）：企业微信群机器人（需注册企业并建内部群）、飞书群机器人（需桌面客户端）、邮件 SMTP。

## 可选工程化项

- [ ] GitHub Actions 接入 `scripts/check.sh`（需要远程仓库）
