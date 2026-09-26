import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/lib/toast";
import { ErrorState } from "@/components/ErrorState";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";
import { Trash2, RefreshCw, Copy, Check } from "lucide-react";
import { SystemConfig } from "@/components/SystemConfig";

type SettingsTab = "system" | "users" | "audit" | "logs";

export function SettingsPage() {
  const user = useAuthStore((s) => s.user);
  const [tab, setTab] = useState<SettingsTab>("system");
  const isAdmin = user?.role === "admin";

  if (!isAdmin) {
    return (
      <div className="card p-6 text-center text-text-muted text-sm">
        只有 admin 可以访问设置
      </div>
    );
  }

  const tabs: { key: SettingsTab; label: string }[] = [
    { key: "system", label: "系统配置" },
    { key: "users", label: "用户管理" },
    { key: "audit", label: "审计日志" },
    { key: "logs", label: "运行日志" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">设置</h1>
      </div>

      <div className="flex gap-0 border-b border-border">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={cn(
              "px-4 py-2 text-sm border-b-2 transition",
              tab === t.key
                ? "border-accent text-text"
                : "border-transparent text-text-muted hover:text-text"
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "system" && <SystemConfig />}
      {tab === "users" && <UserManagement />}
      {tab === "audit" && <AuditLog />}
      {tab === "logs" && <GlobalLogs />}

      {/* At the bottom on purpose: the phone link is a lookup, not a daily
          control, so it should be findable without competing with the tab
          content for attention. */}
      <AccessCard />
    </div>
  );
}

// --- User Management ---

function UserManagement() {
  const me = useAuthStore((s) => s.user);
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [newUser, setNewUser] = useState({ username: "", password: "", role: "viewer" });
  const [message, setMessage] = useState("");

  const [usersError, setUsersError] = useState("");

  const fetchUsers = () => {
    setLoading(true);
    api<any[]>("/api/users")
      .then((rows) => { setUsers(rows); setUsersError(""); })
      .catch((err: unknown) => setUsersError(errorMessage(err, "无法加载用户列表")))
      .finally(() => setLoading(false));
  };

  useEffect(() => { fetchUsers(); }, []);

  const handleCreate = async () => {
    setMessage("");
    try {
      await api("/api/users", { method: "POST", body: newUser });
      setNewUser({ username: "", password: "", role: "viewer" });
      setShowForm(false);
      fetchUsers();
    } catch (err: any) {
      setMessage(err.message || "创建失败");
    }
  };

  const handleDelete = async (userId: number, username: string) => {
    if (!window.confirm(`确定删除用户 '${username}'?`)) return;
    try {
      await api(`/api/users/${userId}`, { method: "DELETE" });
      fetchUsers();
    } catch (err: any) {
      setMessage(err.message || "删除失败");
    }
  };

  const patchUser = async (userId: number, payload: Record<string, unknown>, okMsg = "") => {
    setMessage("");
    try {
      await api(`/api/users/${userId}`, { method: "PATCH", body: payload });
      if (okMsg) setMessage(okMsg);
      fetchUsers();
    } catch (err: any) {
      setMessage(err.message || "操作失败");
    }
  };

  const handleResetPassword = (userId: number, username: string) => {
    const pwd = window.prompt(`为用户 '${username}' 设置新密码（≥6 位）:`);
    if (!pwd) return;
    patchUser(userId, { password: pwd }, `已重置 ${username} 的密码，其会话已下线`);
  };

  const handleForceLogout = (userId: number, username: string) => {
    if (!window.confirm(`强制 '${username}' 所有会话下线？`)) return;
    patchUser(userId, { revoke_sessions: true }, `已下线 ${username} 的所有会话`);
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <button onClick={() => setShowForm(!showForm)} className="btn btn-primary text-xs">
          {showForm ? "取消" : "+ 创建用户"}
        </button>
        {message && <span className="text-xs text-text-muted">{message}</span>}
      </div>

      {showForm && (
        <div className="card p-3 space-y-2">
          <div className="flex gap-2">
            <input className="input flex-1" placeholder="用户名" value={newUser.username}
              onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} />
            <input className="input flex-1" type="password" placeholder="密码 (>=6字符)" value={newUser.password}
              onChange={(e) => setNewUser({ ...newUser, password: e.target.value })} />
            <select className="input w-28" value={newUser.role}
              onChange={(e) => setNewUser({ ...newUser, role: e.target.value })}>
              <option value="viewer">viewer</option>
              <option value="editor">editor</option>
              <option value="admin">admin</option>
            </select>
            <button onClick={handleCreate} className="btn btn-primary">创建</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : usersError ? (
        <ErrorState error={usersError} onRetry={fetchUsers} />
      ) : users.length === 0 ? (
        <div className="card p-6 text-center text-text-muted text-sm">还没有用户</div>
      ) : (
        <div className="card divide-y divide-border text-sm">
          <div className="px-4 py-2 flex items-center gap-3 text-xs text-text-muted uppercase">
            <span className="flex-1">用户名</span>
            <span className="w-24">角色</span>
            <span className="w-32">最后登录</span>
            <span className="w-48">操作</span>
          </div>
          {users.map((u) => {
            const isSelf = me?.id === u.id;
            return (
              <div key={u.id} className={cn("px-4 py-2.5 flex items-center gap-3", u.disabled && "opacity-50")}>
                <span className="flex-1 font-mono flex items-center gap-2">
                  {u.username}
                  {isSelf && <span className="badge border border-accent text-accent text-xs">我</span>}
                  {u.disabled && <span className="badge border border-warning text-warning text-xs">已禁用</span>}
                </span>
                <select
                  className="input w-24 text-xs py-1"
                  value={u.role}
                  disabled={isSelf}
                  onChange={(e) => patchUser(u.id, { role: e.target.value }, `已更新 ${u.username} 的角色`)}
                >
                  <option value="viewer">viewer</option>
                  <option value="editor">editor</option>
                  <option value="admin">admin</option>
                </select>
                <span className="w-32 text-text-muted text-xs">
                  {u.last_login_at?.slice(0, 19)?.replace("T", " ") || "-"}
                </span>
                <div className="w-48 flex items-center gap-1">
                  <button
                    onClick={() => patchUser(u.id, { disabled: !u.disabled },
                      u.disabled ? `已启用 ${u.username}` : `已禁用 ${u.username}`)}
                    disabled={isSelf}
                    className="btn p-1 text-xs"
                    title={u.disabled ? "启用" : "禁用"}
                  >
                    {u.disabled ? "启用" : "禁用"}
                  </button>
                  <button
                    onClick={() => handleResetPassword(u.id, u.username)}
                    className="btn p-1 text-xs"
                    title="重置密码"
                  >
                    重置密码
                  </button>
                  <button
                    onClick={() => handleForceLogout(u.id, u.username)}
                    className="btn p-1 text-xs"
                    title="强制下线"
                  >
                    下线
                  </button>
                  <button
                    onClick={() => handleDelete(u.id, u.username)}
                    disabled={isSelf}
                    className="btn p-1 text-danger"
                    title="删除"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// --- Audit Log ---

function AuditLog() {
  const [entries, setEntries] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [auditError, setAuditError] = useState("");
  const [filter, setFilter] = useState("");

  useEffect(() => {
    api<{ entries: any[] }>("/api/audit?limit=500")
      .then((data) => { setEntries(data.entries); setAuditError(""); })
      .catch((err: unknown) => setAuditError(errorMessage(err, "无法加载审计日志")))
      .finally(() => setLoading(false));
  }, []);

  const needle = filter.trim().toLowerCase();
  const visible = needle
    ? entries.filter((e) =>
        [e.action, e.user, e.target, e.result, e.ip]
          .filter(Boolean)
          .some((v: unknown) => String(v).toLowerCase().includes(needle))
      )
    : entries;

  return (
    <div className="space-y-3">
      <input
        className="input max-w-xs text-xs"
        placeholder="筛选（操作/用户/目标/结果/IP）..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : visible.length === 0 ? (
        <div className="card p-4 text-center text-text-muted text-sm">
          {auditError
            ? `加载失败：${auditError}`
            : entries.length === 0 ? "暂无审计日志" : "无匹配记录"}
        </div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="min-w-full text-xs">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th className="text-left px-3 py-2 font-medium">时间</th>
                <th className="text-left px-3 py-2 font-medium">操作</th>
                <th className="text-left px-3 py-2 font-medium">用户</th>
                <th className="text-left px-3 py-2 font-medium">目标</th>
                <th className="text-left px-3 py-2 font-medium">结果</th>
                <th className="text-left px-3 py-2 font-medium">IP</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((e, i) => (
                <tr key={i} className="border-b border-border hover:bg-bg-hover">
                  <td className="px-3 py-2 whitespace-nowrap">{e.ts?.slice(0, 19)?.replace("T", " ")}</td>
                  <td className="px-3 py-2 whitespace-nowrap font-mono">{e.action}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{e.user || "-"}</td>
                  <td className="px-3 py-2 whitespace-nowrap">{e.target || "-"}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={cn("badge",
                      e.result === "success" ? "text-success" :
                      e.result === "failed" ? "text-danger" : "text-warning"
                    )}>{e.result}</span>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{e.ip || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// --- Global Logs ---

function GlobalLogs() {
  const [lines, setLines] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [follow, setFollow] = useState(false);
  const [linesError, setLinesError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api<{ lines: string[] }>("/api/logs/global?lines=500")
        .then((data) => { if (!cancelled) { setLines(data.lines); setLinesError(""); } })
        .catch((err: unknown) => {
          if (!cancelled) setLinesError(errorMessage(err, "无法加载全局日志"));
        })
        .finally(() => { if (!cancelled) setLoading(false); });
    load();
    if (!follow) return () => { cancelled = true; };
    const timer = window.setInterval(load, 5000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [follow]);

  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-xs text-text-muted cursor-pointer">
        <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
        自动刷新（5s）
      </label>
      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : lines.length === 0 ? (
        <div className="card p-4 text-center text-text-muted text-sm">
          {linesError ? `加载失败：${linesError}` : "暂无日志"}
        </div>
      ) : (
        <pre className="card p-3 text-xs font-mono leading-relaxed max-h-[70vh] overflow-y-auto bg-[#0d1117]">
          {lines.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap break-all text-text-muted">{l}</div>
          ))}
        </pre>
      )}

      {/* Server management */}
      <div className="border-t border-border pt-4 mt-6">
        <h3 className="text-sm font-medium mb-2">服务器管理</h3>
        <div className="card p-3 flex items-center gap-3">
          <div className="flex-1 text-sm text-text-muted">
            关闭服务器会断开所有用户连接。仅在维护或升级时使用。
          </div>
          <ShutdownButton />
        </div>
      </div>

    </div>
  );
}

interface GatewayInfo {
  enabled: boolean;
  lan_access: boolean;
  local_url: string;
  lan_url: string;
  lan_address: string;
  public_port: number;
  app_port: number;
  secure_cookies: boolean;
  restart_required?: boolean;
  restart_hint?: string;
}

/** A single link with an icon-only copy button: compact, and still pasteable. */
function UrlChip({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      // Clipboard needs a secure context; on plain-HTTP LAN the read-only
      // field below is still selectable by hand.
      const field = document.getElementById(`url-${label}`) as HTMLInputElement | null;
      field?.select();
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return <span className="inline-flex items-center gap-1">
    <span className="text-text-muted">{label}</span>
    <input id={`url-${label}`} readOnly value={url}
      onFocus={(e) => e.currentTarget.select()}
      aria-label={`${label}链接`}
      className="input font-mono text-xs w-44 sm:w-52 py-0.5" />
    <button className="btn text-xs py-0.5 px-1.5 shrink-0" onClick={copy}
      aria-label={`复制${label}链接`} title={copied ? "已复制" : "复制"}>
      {copied ? <Check className="w-3 h-3 text-success" /> : <Copy className="w-3 h-3" />}
    </button>
  </span>;
}

/**
 * Access addresses, kept deliberately quiet.
 *
 * This is a lookup ("what's the phone link again?"), not a control you touch
 * daily, so it lives at the bottom of the page as a single line and keeps the
 * LAN switch and its warnings behind a disclosure.
 */
function AccessCard() {
  const [info, setInfo] = useState<GatewayInfo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [restart, setRestart] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInfo(await api<GatewayInfo>("/api/gateway"));
    } catch (err) {
      setError(errorMessage(err, "读取访问地址失败"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const toggle = async (next: boolean) => {
    setBusy(true); setError(""); setRestart("");
    try {
      const result = await api<GatewayInfo>("/api/gateway", {
        method: "PUT", body: { lan_access: next },
      });
      setInfo(result);
      if (result.restart_required) setRestart(result.restart_hint || "");
    } catch (err) {
      setError(errorMessage(err, "保存失败"));
    } finally {
      setBusy(false);
    }
  };

  if (error && !info) {
    return <div role="alert" className="text-xs text-text-muted">读取访问地址失败：{error}</div>;
  }
  if (!info) return null;

  const phone = info.lan_access && info.lan_url ? info.lan_url : "";

  return <div className="border-t border-border pt-3 mt-2">
    <details className="text-xs">
      <summary className="cursor-pointer text-text-muted select-none flex flex-wrap items-center gap-2">
        <span>访问地址</span>
        {info.lan_access
          ? <span className="text-success">· 手机可访问</span>
          : <span>· 仅本机</span>}
        {/* The links stay in the summary line: this is the thing you came to
            read, and it should not require expanding anything. */}
        <span className="flex flex-wrap items-center gap-3 ml-1" onClick={(e) => e.preventDefault()}>
          <UrlChip label="本机" url={info.local_url} />
          {phone ? <UrlChip label="手机" url={phone} /> : null}
        </span>
        <button className="btn text-xs py-0.5 px-1.5 ml-auto shrink-0" onClick={load}
          disabled={busy} aria-label="刷新访问地址" title="刷新">
          <RefreshCw className={cn("w-3 h-3", loading && "animate-spin")} />
        </button>
      </summary>

      <div className="mt-2.5 space-y-2 pl-1">
        {error ? <div role="alert" className="text-danger">{error}</div> : null}

        {!info.enabled ? (
          <p className="text-text-muted">
            常驻网关未启用，应用直接监听 {info.public_port} 端口。把
            <code className="mx-1">config/web.yaml</code> 的
            <code className="mx-1">gateway.enabled</code> 设为 true，再运行
            <code className="mx-1">bash scripts/install_launchd.sh</code>，
            收藏的地址就能在应用退出后自动拉起。
          </p>
        ) : (
          <>
            <p className="text-text-muted">
              收藏「本机」地址：应用退出、崩溃甚至重启后打开它，会自动拉起（约 12 秒）。
              {!phone && " 手机访问未开启，展开后可开启。"}
            </p>
            <label className="flex items-start gap-2 text-text-muted cursor-pointer">
              <input type="checkbox" className="mt-0.5" checked={info.lan_access}
                disabled={busy} onChange={(e) => toggle(e.target.checked)} />
              <span>允许同一 WiFi 下的设备访问
                {!info.secure_cookies && (
                  <span className="block text-text-muted">
                    开启后网关监听 0.0.0.0。当前是 HTTP 明文，账号密码会在网络中传输，请只在可信网络开启。
                  </span>
                )}
              </span>
            </label>
          </>
        )}

        {restart ? (
          <div role="alert" className="text-warning space-y-1">
            <p>配置已保存，但监听地址要重启网关才生效：</p>
            <code className="block bg-bg-hover rounded px-2 py-1 font-mono break-all">{restart}</code>
          </div>
        ) : null}
      </div>
    </details>
  </div>;
}

function ShutdownButton() {
  const [confirming, setConfirming] = useState(false);
  const [shuttingDown, setShuttingDown] = useState(false);
  const [error, setError] = useState("");

  const handleShutdown = async () => {
    setShuttingDown(true);
    setError("");
    try {
      await api("/api/deploy/shutdown", { method: "POST" });
      // Server might not respond after this
      setTimeout(() => {
        window.location.href = "/";
      }, 2000);
    } catch (err: any) {
      setError(err.message || "关闭失败");
      setShuttingDown(false);
    }
  };

  if (confirming) {
    return (
      <div className="flex items-center gap-2">
        <span className="text-xs text-danger">确认关闭？</span>
        <button onClick={handleShutdown} disabled={shuttingDown} className="btn btn-danger text-xs">
          {shuttingDown ? "关闭中..." : "确认关闭"}
        </button>
        <button onClick={() => setConfirming(false)} className="btn text-xs">取消</button>
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    );
  }

  return (
    <button onClick={() => setConfirming(true)} className="btn btn-danger text-xs">
      关闭服务器
    </button>
  );
}
