import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getSetupStatus, type SetupStatus } from "@/api";
import { useAuthStore } from "@/lib/auth-store";

const LAST_USER_KEY = "arec.last-username";

function rememberedUsername(): string {
  try {
    return localStorage.getItem(LAST_USER_KEY) || "";
  } catch {
    return "";
  }
}

export function LoginPage() {
  const [username, setUsername] = useState(rememberedUsername);
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<SetupStatus | null>(null);
  const login = useAuthStore((s) => s.login);
  const user = useAuthStore((s) => s.user);
  const navigate = useNavigate();

  useEffect(() => {
    getSetupStatus().then(setSetup).catch(() => setSetup(null));
  }, []);

  // A live session should never see this form: a still-valid refresh cookie
  // means the user is already signed in, and asking again is the complaint
  // "remember me" exists to solve.
  useEffect(() => {
    if (user) navigate("/", { replace: true });
  }, [user, navigate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(username.trim(), password, remember);
      // Only the username is kept, and only so the field is prefilled. The
      // password is never written to storage.
      try {
        if (remember) localStorage.setItem(LAST_USER_KEY, username.trim());
        else localStorage.removeItem(LAST_USER_KEY);
      } catch {
        // A browser with storage disabled still logs in fine.
      }
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
            <label htmlFor="login-username" className="block text-sm text-text-muted mb-1">用户名</label>
            <input
              id="login-username"
              className="input"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus={!username}
              required
            />
          </div>
          <div>
            <label htmlFor="login-password" className="block text-sm text-text-muted mb-1">密码</label>
            <input
              id="login-password"
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-text-muted">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            记住我（30 天内免登录）
          </label>
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
