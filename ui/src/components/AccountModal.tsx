import { useEffect, useState } from "react";
import { changePassword, listMySessions, revokeMySession, logout, listApiTokens, createApiToken, revokeApiToken, type ApiToken } from "@/api";
import { useAuthStore } from "@/lib/auth-store";
import { useToast } from "@/lib/toast";
import { X } from "lucide-react";

interface Session {
  jti: string;
  kind: string;
  expires_at: number;
  current: boolean;
}

export function AccountModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const user = useAuthStore((s) => s.user);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const toast = useToast();
  const [err, setErr] = useState("");
  const [sessions, setSessions] = useState<Session[]>([]);
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [newToken, setNewToken] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setMsg("");
    setErr("");
    setCurrent("");
    setNext("");
    setConfirm("");
    listMySessions()
      .then((d) => setSessions(d.sessions || []))
      .catch(() => setSessions([]));
    listApiTokens()
      .then((d) => setTokens(d.tokens || []))
      .catch(() => setTokens([]));
  }, [open]);

  const handleCreateToken = async () => {
    const name = window.prompt("Token 名称（便于识别用途，如 ci-bot）:", "api-token");
    if (!name) return;
    const daysRaw = window.prompt("有效期天数（留空 = 永不过期）:", "");
    if (daysRaw === null) return;
    const days = daysRaw.trim() ? Number(daysRaw.trim()) : undefined;
    if (days !== undefined && (!Number.isInteger(days) || days <= 0)) {
      setErr("有效期需为正整数天数");
      return;
    }
    try {
      const created = await createApiToken(name, days);
      setNewToken(created.token);
      const d = await listApiTokens().catch(() => null);
      if (d) setTokens(d.tokens || []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "创建失败");
    }
  };

  const handleRevokeToken = async (id: number) => {
    if (!window.confirm("撤销该 token？使用它的脚本会立即失效。")) return;
    try {
      await revokeApiToken(id);
      setTokens((prev) => prev.map((t) => (t.id === id ? { ...t, revoked: true } : t)));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "撤销失败");
    }
  };

  if (!open) return null;

  const handleSubmit = async () => {
    setMsg("");
    setErr("");
    if (next.length < 6) {
      setErr("新密码至少 6 个字符");
      return;
    }
    if (next !== confirm) {
      setErr("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await changePassword(current, next);
      setMsg("密码已修改，其他设备已下线");
      setCurrent("");
      setNext("");
      setConfirm("");
      const d = await listMySessions().catch(() => null);
      if (d) setSessions(d.sessions || []);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "修改失败");
    } finally {
      setBusy(false);
    }
  };

  const handleRevoke = async (jti: string, isCurrent: boolean) => {
    try {
      await revokeMySession(jti);
      if (isCurrent) {
        await logout().catch(() => {});
        window.location.href = "/login";
        return;
      }
      setSessions((prev) => prev.filter((s) => s.jti !== jti));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "撤销失败");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        className="card p-5 w-full max-w-md space-y-4 max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center">
          <h2 className="text-sm font-semibold flex-1">账号设置</h2>
          <button onClick={onClose} className="btn p-1" aria-label="关闭">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="text-xs text-text-muted">
          {user?.username} · {user?.role}
        </div>

        <div className="space-y-2">
          <h3 className="text-xs font-medium text-text-muted uppercase">修改密码</h3>
          <input
            className="input"
            type="password"
            placeholder="当前密码"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
          <input
            className="input"
            type="password"
            placeholder="新密码（≥6 位）"
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
          <input
            className="input"
            type="password"
            placeholder="确认新密码"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          {msg && <div className="text-xs text-success">{msg}</div>}
          {err && <div className="text-xs text-danger">{err}</div>}
          <button
            onClick={handleSubmit}
            disabled={busy || !current || !next}
            className="btn btn-primary text-xs"
          >
            {busy ? "提交中..." : "修改密码"}
          </button>
        </div>

        <div className="space-y-2 pt-2 border-t border-border">
          <h3 className="text-xs font-medium text-text-muted uppercase">API Tokens</h3>
          <div className="text-xs text-text-muted">
            用于脚本/CI 以 <code className="bg-bg-hover px-1 rounded">Authorization: Bearer &lt;token&gt;</code> 调用 API
          </div>
          <button onClick={handleCreateToken} className="btn text-xs">+ 创建 Token</button>
          {newToken && (
            <div className="text-xs bg-bg-hover border border-accent/50 rounded-md p-2 space-y-1">
              <div className="text-warning">仅显示一次，请立即复制：</div>
              <code className="block break-all font-mono">{newToken}</code>
              <button
                className="btn text-xs"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(newToken);
                    toast.success("Token 已复制");
                  } catch {
                    toast.error("复制失败", "浏览器拒绝了剪贴板访问，请手动选中上方 Token 复制");
                  }
                }}
              >
                复制
              </button>
            </div>
          )}
          {tokens.filter((t) => !t.revoked).length > 0 && (
            <div className="space-y-1.5">
              {tokens.filter((t) => !t.revoked).map((t) => (
                <div key={t.id} className="flex items-center gap-2 text-xs">
                  <span className="font-mono">{t.prefix}…</span>
                  <span className="text-text-muted">{t.name}</span>
                  <span className="text-text-muted">
                    {t.last_used_at ? `used ${String(t.last_used_at).slice(0, 10)}` : "未使用"}
                  </span>
                  <span className="text-text-muted">
                    {t.expires_at
                      ? `至 ${new Date(t.expires_at * 1000).toLocaleDateString()}`
                      : "永不过期"}
                  </span>
                  <button
                    onClick={() => handleRevokeToken(t.id)}
                    className="ml-auto text-danger hover:underline"
                  >
                    撤销
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-2 pt-2 border-t border-border">
          <h3 className="text-xs font-medium text-text-muted uppercase">活跃会话</h3>
          {sessions.length === 0 ? (
            <div className="text-xs text-text-muted">暂无会话</div>
          ) : (
            <div className="space-y-1.5">
              {sessions.map((s) => (
                <div key={s.jti} className="flex items-center gap-2 text-xs">
                  <span className="font-mono text-text-muted">{s.jti.slice(0, 10)}…</span>
                  <span className="text-text-muted">{s.kind}</span>
                  <span className="text-text-muted">
                    {new Date(s.expires_at * 1000).toLocaleString()}
                  </span>
                  {s.current && <span className="badge border border-accent text-accent">当前</span>}
                  <button
                    onClick={() => handleRevoke(s.jti, s.current)}
                    className="ml-auto text-danger hover:underline"
                  >
                    {s.current ? "退出" : "撤销"}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
