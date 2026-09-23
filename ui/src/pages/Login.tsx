import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getSetupStatus, type SetupStatus } from "@/api";
import { useAuthStore } from "@/lib/auth-store";

export function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const login = useAuthStore((s) => s.login);
  const navigate = useNavigate();

  useEffect(() => {
    getSetupStatus().then(setSetup).catch(() => setSetup(null));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-screen items-center justify-center bg-bg">
      <div className="w-full max-w-sm px-6">
        <h1 className="text-xl font-semibold text-center mb-6">AI Research Console</h1>
        {setup?.needs_admin && (
          <div className="card p-3 mb-4 text-xs space-y-1 border-accent/50">
            <div className="font-medium">首次使用</div>
            <div className="text-text-muted">
              还没有账号。请在项目目录运行：
            </div>
            <code className="block bg-bg-hover rounded px-2 py-1 font-mono">
              python run_web.py create-admin &lt;用户名&gt; &lt;密码&gt;
            </code>
          </div>
        )}
        {setup && !setup.needs_admin && !setup.llm_configured && (
          <div className="card p-3 mb-4 text-xs text-text-muted border-warning/50">
            未检测到 LLM API Key（.env 的 LLM_API_KEY），运行任务前请先配置。
          </div>
        )}
        <form onSubmit={handleSubmit} className="card p-6 space-y-4">
          <div>
            <label className="block text-sm text-text-muted mb-1">用户名</label>
            <input
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
              required
            />
          </div>
          <div>
            <label className="block text-sm text-text-muted mb-1">密码</label>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          {error && (
            <p className="text-sm text-danger">{error}</p>
          )}
          <button
            type="submit"
            disabled={busy}
            className="btn btn-primary w-full"
          >
            {busy ? "登录中..." : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}
