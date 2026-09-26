# 常驻网关与局域网访问

## 为什么需要网关

30 分钟空闲退出的只是启动器 applet，服务进程原本会继续运行，所以 URL 平时不会
因为空闲而失效。真正让收藏的 URL 失效的是：机器重启、服务崩溃、或 applet 的
`stopStaleServer` 回收了无响应的进程 —— 此时没有任何进程监听 8765，浏览器直接
连接被拒，而浏览器本身无法把应用拉起来。

网关补上这一段：**它常驻占住对外端口，把应用当作按需启动的子进程。**

```
浏览器 / 手机 ──▶ 网关 :8765 ──▶ 应用 :8766（仅 127.0.0.1）
                      │
                      └── 应用不在？拉起它，显示「启动中」，就绪后继续
```

## 组成

| 部件 | 位置 | 职责 |
|------|------|------|
| 网关进程 | `scripts/gateway.py` | 占住对外端口、反向代理、按需拉起应用 |
| 配置 | `config/web.yaml` 的 `gateway` 段 | 开关、内部端口、局域网开关 |
| 常驻 | `scripts/launchd/com.arec.gateway.plist` | `KeepAlive` + `RunAtLoad`，开机自启 |
| 安装 | `bash scripts/install_launchd.sh` | 仅在 `gateway.enabled: true` 时安装 |
| 设置 API | `GET/PUT /api/gateway` | 读取状态、切换局域网访问（admin + CSRF + 审计） |
| 局域网地址 | `utils/net.py` | 枚举网卡并过滤，**网关与 API 共用同一实现** |

## 关键设计约束

**只用标准库。** 这是其他一切坏掉时仍要能工作的那个进程，不能依赖项目虚拟环境
健康。`scripts/gateway.py` 只 import 标准库和 `utils/net.py`。

**只在收到真实请求时拉起。** 网关不做后台轮询。若它轮询，空闲退出后下一次轮询就会
把应用复活，30 分钟规则等于被悄悄废除。applet 自己的看门狗也算请求，这正是需要的
自愈行为。

**启动窗口内不重复拉起。** 应用先绑定端口、再完成启动工作（仅报告索引预热就要
约 12 秒），在这段时间里它不响应任何请求。因此 `Supervisor.ensure()` 除了健康探测
外还会检查端口是否已被占用（`claimed()`）：端口有人占但答不上来，说明它正在启动，
而不是崩了。只有冷却时间是不够的 —— 冷却过期后的一个请求会再拉起一个抢不到端口的
进程。

**流式转发，不缓冲。** 任务的实时日志走 SSE。`http.client` 已经解开上游的 chunked，
所以网关只按「有没有 `Content-Length`」决定如何重新分帧；并且必须用 `read1()`
而不是 `read()` —— 后者会等到缓冲区填满，实时日志会变成结束后一次性出现的一坨。

**只有一个进程监听对外地址。** 应用永远只绑 `127.0.0.1`；`bind_host()` 只有网关会
返回 `0.0.0.0`。`gateway.enabled: false` 时行为与从前完全一致（应用直接监听
`server.port`）。

## 局域网访问

默认关闭。开启后网关绑定 `0.0.0.0`，同一 WiFi 下的设备可以打开登录页。

- 仍然需要账号密码，登录失败照常限流（5 次/分）并写审计。
- `cookie_secure: false`，所以局域网上是 **HTTP 明文**，凭证会在网络中传输。
  家用 WiFi 可接受，但请知情。
- 改开关会写 `config/web.yaml` 并返回 `restart_required: true`：监听方不能在正在
  处理的请求里重绑自己，所以必须重启网关（页面会给出确切命令）。
- `gateway.enabled` 故意**不**提供 HTTP 开关：它会搬动应用端口并需要重装 launchd
  代理，一个 API 调用不该假装能做到。

### 局域网地址的挑选

曾经用「开一个 UDP socket 连到公网地址再读 `getsockname()`」来取本机 IP。在装有
VPN / TUN 的 macOS 上这会返回隧道地址（本机实测 `198.18.0.1`，属于 RFC 2544
benchmark 段），手机上根本打不开。现在改为枚举网卡并过滤：只接受 RFC 1918 私有
地址，排除 loopback、`169.254/16`（未连通的接口）和 `198.18.0.0/15`（TUN），并
优先 `en0`/`en1` 这类硬件网卡。宁可显示为空，也不显示一个打不开的链接。

## 维护

网关不负责清理；`com.arec.maintenance` 每天 04:30 跑一次
`scripts/maintenance.py`（在每日 06:00 轮次之前）：

| 步骤 | 处理的问题 |
|------|-----------|
| `VACUUM` + WAL checkpoint | `reports.db` 每次重建索引都留下空闲页，实测回收 46 MB（161 MB → 98.6 MB） |
| 清理过期 session | `users.db` 每次登录 +2 行、每次 token 刷新 +1 行，`cleanup_expired()` 此前从未被调用（202 → 101） |
| 清理 `.lock` sidecar | `file_lock` 释放 flock 但不删文件，每个路径永久留一个 |
| 清理日志（**含子目录**） | 旧 `cleanup_logs.py` 只 glob `logs/*.log*`，够不到 `logs/schedules`、`logs/jobs`、`logs/openai` |
| 清理备份 | 快照目录与每次配置保存产生的 `.yaml` 各自封顶 |

数据库被应用占用时 VACUUM 会失败，脚本会**报告**而不是当作成功。干跑：

```bash
python scripts/maintenance.py --dry-run
```

## 备份

`scripts/backup_state.sh` 默认**不含** `state/reports.db`：它是派生产物，
`python run_web.py reindex` 可从 `output/**.md` 完全重建，而它有 150 MB，
每份快照都带一份会让备份目录失控（实测 14 份到了 199 MB）。需要时用
`--with-index`。

保留份数由 `AREC_BACKUP_KEEP`（默认 14）控制，快照目录与 `.yaml` 各自封顶。
**`scripts/backup_state.sh` 本身不自动运行** —— 需要自己安排（launchd 或 cron）。

## 运维

```bash
python scripts/gateway.py --status   # 只报告状态，不监听
curl -s http://127.0.0.1:8765/__gateway/status
launchctl print gui/$(id -u)/com.arec.gateway | head -20
tail -f logs/gateway.log logs/gateway.err.log logs/gateway_app.log
```

`__gateway/status` 里的 `spawns` 是累计拉起次数；持续增长说明应用反复崩溃，
看 `logs/gateway_app.log`。`last_error` 非空则是拉起本身失败（最常见是
`state/server.pid` 指向一个已被杀掉的进程）。

关闭网关：把 `gateway.enabled` 改回 `false`，然后
`bash scripts/install_launchd.sh --uninstall && bash scripts/install_launchd.sh`
（卸载会一并移除 `com.arec.gateway`）。

## 测试

`tests/test_gateway.py`（22 个）全部跑在临时端口和假上游上，不碰真实应用或真实
网卡设置。覆盖：代理转发请求体与 CSRF 头、状态端点不被代理、**SSE 逐条到达而非
缓冲**（上游保持连接时第一条事件必须先到）、冷启动只拉起一次、启动窗口内不重复
拉起、冷却期不重试、拉起失败要报错而不是吞掉、局域网地址过滤掉 VPN 隧道地址。
