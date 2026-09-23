import { lazy, Suspense, useEffect, useState } from "react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import {
  CheckCircle2, FileCode, Plug, RotateCcw, Save, SlidersHorizontal, XCircle,
} from "lucide-react";

const YamlEditor = lazy(() =>
  import("@/components/YamlEditor").then((m) => ({ default: m.YamlEditor })));

type Mode = "form" | "yaml";

interface SecretInfo {
  env: string;
  label: string;
  configured: boolean;
  masked: string;
}

const NOTIFY_EVENTS = [
  ["failed", "任务失败"],
  ["cancelled", "任务取消"],
  ["success", "任务成功"],
  ["round_finished", "轮次完成"],
] as const;

const LLM_KEYS = ["model", "base_url", "api_key_env", "timeout", "request_timeout",
  "max_tokens", "temperature", "stream", "extra_headers"];
const EMBED_KEYS = ["embedding_model", "embedding_base_url", "embedding_api_key_env"];
const SEARCH_KEYS = ["provider", "mode", "api_key_env", "api_key_envs", "max_results",
  "timeout", "fallback_provider", "fallback_providers"];

function Field({ label, hint, children }: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <div className="text-xs text-text-muted">
        {label}
        {hint && <span className="ml-1 opacity-70">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

function Toggle({ label, checked, onChange }: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-xs cursor-pointer py-1">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="accent-[var(--accent,#3b82f6)] w-4 h-4"
      />
      {label}
    </label>
  );
}

function Section({ title, children, action }: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <div className="text-sm font-medium flex-1">{title}</div>
        {action}
      </div>
      {children}
    </div>
  );
}

function TestBadge({ state, testing }: { state: any; testing: boolean }) {
  if (testing) {
    return <span className="text-[11px] text-text-muted animate-pulse">测试中…</span>;
  }
  if (!state) return null;
  return (
    <span className={cn("inline-flex items-center gap-1 text-[11px]",
      state.ok ? "text-success" : "text-danger")}>
      {state.ok ? <CheckCircle2 className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
      {state.ok
        ? `可用 · ${state.latency_ms}ms${state.dims ? ` · ${state.dims} 维` : ""}`
        : state.error || "失败"}
    </span>
  );
}

export function SystemConfig() {
  const [mode, setMode] = useState<Mode>("form");
  const [parsed, setParsed] = useState<any>(null);
  const [content, setContent] = useState("");
  const [mtime, setMtime] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);

  const [secrets, setSecrets] = useState<SecretInfo[]>([]);
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({});
  const [cleared, setCleared] = useState<string[]>([]);
  const [savingSecrets, setSavingSecrets] = useState(false);
  const [secretMessage, setSecretMessage] = useState("");

  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, any>>({});

  const load = () => {
    setLoading(true);
    setError("");
    setConflict(false);
    api<any>("/api/config/system")
      .then((data) => {
        setParsed(data.parsed || {});
        setContent(data.content || "");
        setMtime(typeof data.mtime === "number" ? data.mtime : null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
    api<any>("/api/config/secrets")
      .then((data) => {
        setSecrets(data.secrets || []);
        setSecretInputs({});
        setCleared([]);
      })
      .catch(() => setSecrets([]));
  };

  useEffect(() => {
    load();
  }, []);

  const get = (path: string[], fallback?: unknown) => {
    let node = parsed;
    for (const key of path) {
      if (node == null || typeof node !== "object") return fallback;
      node = node[key];
    }
    return node === undefined ? fallback : node;
  };

  const set = (path: string[], value: unknown) => {
    setParsed((prev: any) => {
      const next = JSON.parse(JSON.stringify(prev ?? {}));
      let node = next;
      for (let i = 0; i < path.length - 1; i++) {
        if (typeof node[path[i]] !== "object" || node[path[i]] === null) {
          node[path[i]] = {};
        }
        node = node[path[i]];
      }
      if (value === undefined) {
        delete node[path[path.length - 1]];
      } else {
        node[path[path.length - 1]] = value;
      }
      return next;
    });
  };

  const pick = (source: any, keys: string[]) => {
    const out: Record<string, unknown> = {};
    for (const key of keys) {
      if (source && key in source) out[key] = source[key];
    }
    return out;
  };

  const buildPatch = () => ({
    ai: pick(parsed?.ai, [...LLM_KEYS, ...EMBED_KEYS]),
    search: pick(parsed?.search, SEARCH_KEYS),
    research: pick(parsed?.research, ["mode", "max_rounds", "max_searches"]),
    budget: pick(parsed?.budget, ["monthly_usd_limit", "warn_ratio", "block_pipeline"]),
    notifications: {
      ...pick(parsed?.notifications, ["enabled", "notify_on"]),
      digest: pick(parsed?.notifications?.digest, ["enabled", "max_actions"]),
    },
    extraction: pick(parsed?.extraction, ["enabled", "model", "base_url", "api_key_env"]),
  });

  const handleSave = async () => {
    setSaving(true);
    setMessage("");
    setError("");
    setConflict(false);
    try {
      const body = mode === "form"
        ? { patch: buildPatch(), expected_mtime: mtime }
        : { content, expected_mtime: mtime };
      const resp = await api<{ ok: boolean; mtime: number }>("/api/config/system", {
        method: "PUT",
        body,
      });
      if (typeof resp?.mtime === "number") setMtime(resp.mtime);
      setMessage("保存成功");
      if (mode === "form") load();
    } catch (err: any) {
      if (err?.code === "conflict") {
        setConflict(true);
        setError("配置已被外部修改，请重新加载后再保存");
      } else {
        setError(err.message || "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSaveSecrets = async () => {
    const values: Record<string, string | null> = {};
    for (const item of secrets) {
      if (cleared.includes(item.env)) values[item.env] = null;
      else if (secretInputs[item.env]) values[item.env] = secretInputs[item.env];
    }
    if (Object.keys(values).length === 0) {
      setSecretMessage("没有需要保存的密钥变更");
      return;
    }
    setSavingSecrets(true);
    setSecretMessage("");
    setError("");
    try {
      const resp = await api<any>("/api/config/secrets", {
        method: "PUT",
        body: { values },
      });
      setSecrets(resp.secrets || []);
      setSecretInputs({});
      setCleared([]);
      setSecretMessage("密钥已保存并立即生效（写入 .env，不进版本库）");
    } catch (err: any) {
      setError(err.message || "保存密钥失败");
    } finally {
      setSavingSecrets(false);
    }
  };

  const runTest = async (key: string, path: string, configPatch: any, envName?: string) => {
    setTesting(key);
    setError("");
    setTestResults((prev) => ({ ...prev, [key]: null }));
    try {
      const typedKey = envName ? secretInputs[envName] || undefined : undefined;
      const result = await api<any>(path, {
        method: "POST",
        body: { config: configPatch, api_key: typedKey },
      });
      setTestResults((prev) => ({ ...prev, [key]: result }));
    } catch (err: any) {
      setTestResults((prev) => ({
        ...prev,
        [key]: { ok: false, error: err.message || "测试失败" },
      }));
    } finally {
      setTesting(null);
    }
  };

  const numInput = (path: string[], opts: { step?: string; placeholder?: string } = {}) => (
    <input
      className="input"
      type="number"
      step={opts.step}
      placeholder={opts.placeholder}
      value={get(path) ?? ""}
      onChange={(e) => set(path, e.target.value === "" ? undefined : Number(e.target.value))}
    />
  );

  const textInput = (path: string[], placeholder?: string, mono = false) => (
    <input
      className={cn("input", mono && "font-mono text-xs")}
      placeholder={placeholder}
      value={get(path, "") ?? ""}
      onChange={(e) => set(path, e.target.value)}
    />
  );

  const selectInput = (path: string[], options: string[]) => (
    <select
      className="input"
      value={get(path, options[0]) ?? options[0]}
      onChange={(e) => set(path, e.target.value)}
    >
      {options.map((option) => <option key={option} value={option}>{option}</option>)}
    </select>
  );

  if (loading) return <div className="text-sm text-text-muted">加载中...</div>;

  const notifyOn: string[] = get(["notifications", "notify_on"], []) || [];
  const llmEnv = get(["ai", "api_key_env"], "LLM_API_KEY");
  const embedEnv = get(["ai", "embedding_api_key_env"], "");
  const searchProvider = get(["search", "provider"], "parallel");
  const searchEnvMap = get(["search", "api_key_envs"], {}) || {};
  const searchEnv = searchEnvMap[searchProvider] || get(["search", "api_key_env"], "");

  const TestButton = ({ id, label, path, configPatch, envName }: {
    id: string;
    label: string;
    path: string;
    configPatch: any;
    envName?: string;
  }) => (
    <div className="flex items-center gap-2">
      <button
        onClick={() => runTest(id, path, configPatch, envName)}
        disabled={testing != null}
        className="btn text-xs"
      >
        <Plug className="w-3 h-3" />
        {label}
      </button>
      <TestBadge state={testResults[id]} testing={testing === id} />
    </div>
  );

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex rounded-md border border-border overflow-hidden">
          <button
            onClick={() => { setMode("form"); load(); }}
            className={cn("px-3 py-1.5 text-xs flex items-center gap-1.5",
              mode === "form" ? "bg-accent text-white" : "text-text-muted hover:bg-bg-hover")}
          >
            <SlidersHorizontal className="w-3 h-3" />
            表单
          </button>
          <button
            onClick={() => { setMode("yaml"); load(); }}
            className={cn("px-3 py-1.5 text-xs flex items-center gap-1.5",
              mode === "yaml" ? "bg-accent text-white" : "text-text-muted hover:bg-bg-hover")}
          >
            <FileCode className="w-3 h-3" />
            YAML（高级）
          </button>
        </div>
        {mode === "form" && (
          <button onClick={handleSave} disabled={saving} className="btn btn-primary text-xs">
            <Save className="w-3.5 h-3.5" />
            {saving ? "保存中..." : "保存配置"}
          </button>
        )}
        {mode === "yaml" && (
          <button onClick={handleSave} disabled={saving} className="btn btn-primary text-xs">
            <Save className="w-3.5 h-3.5" />
            {saving ? "保存中..." : "保存 YAML"}
          </button>
        )}
        {conflict && (
          <button onClick={load} className="btn text-xs">
            <RotateCcw className="w-3 h-3" />
            重新加载
          </button>
        )}
        {message && <span className="text-xs text-success">{message}</span>}
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>

      {mode === "yaml" ? (
        <Suspense fallback={<div className="text-sm text-text-muted py-6 text-center">加载编辑器...</div>}>
          <YamlEditor value={content} onChange={setContent} height="calc(100vh - 340px)" />
        </Suspense>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          <Section
            title="API 密钥"
            action={(
              <button onClick={handleSaveSecrets} disabled={savingSecrets} className="btn text-xs">
                <Save className="w-3 h-3" />
                {savingSecrets ? "保存中..." : "保存密钥"}
              </button>
            )}
          >
            {secrets.length === 0 ? (
              <div className="text-xs text-text-muted">
                当前配置未引用任何密钥环境变量（本地端点无需密钥）。
              </div>
            ) : (
              <div className="space-y-2">
                {secrets.map((item) => (
                  <div key={item.env} className="flex items-center gap-2">
                    <div className="w-40 shrink-0">
                      <div className="text-xs">{item.label}</div>
                      <div className="text-[10px] text-text-muted font-mono">{item.env}</div>
                    </div>
                    <input
                      className="input flex-1 font-mono text-xs"
                      type="password"
                      placeholder={
                        cleared.includes(item.env)
                          ? "已清除（保存后生效）"
                          : item.configured ? `已配置 · ${item.masked}` : "未配置"
                      }
                      value={secretInputs[item.env] || ""}
                      onChange={(e) => {
                        setSecretInputs((prev) => ({ ...prev, [item.env]: e.target.value }));
                        setCleared((prev) => prev.filter((e) => e !== item.env));
                      }}
                    />
                    {item.configured && !cleared.includes(item.env) && !secretInputs[item.env] && (
                      <button
                        onClick={() => setCleared((prev) => [...prev, item.env])}
                        className="text-[11px] text-text-muted hover:text-danger"
                        title="保存后从 .env 移除"
                      >
                        清除
                      </button>
                    )}
                    {cleared.includes(item.env) && (
                      <button
                        onClick={() => setCleared((prev) => prev.filter((e) => e !== item.env))}
                        className="text-[11px] text-text-muted hover:text-text"
                      >
                        撤销
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
            {secretMessage && <div className="text-xs text-success">{secretMessage}</div>}
            <div className="text-[11px] text-text-muted">
              密钥写入项目 .env（不进版本库），保存后立即生效，无需重启服务。
            </div>
          </Section>

          <Section
            title="AI 模型（LLM）"
            action={(
              <TestButton
                id="llm"
                label="测试 LLM"
                path="/api/config/test-llm"
                configPatch={{ ai: pick(parsed?.ai, LLM_KEYS) }}
                envName={llmEnv}
              />
            )}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="模型 ID">
                <div>{textInput(["ai", "model"], "deepseek-v4.1-flash", true)}</div>
              </Field>
              <Field label="接口地址" hint="OpenAI 兼容">
                <div>{textInput(["ai", "base_url"], "https://.../v1", true)}</div>
              </Field>
              <Field label="密钥环境变量" hint="对应上方密钥卡片">
                <div>{textInput(["ai", "api_key_env"], "LLM_API_KEY", true)}</div>
              </Field>
              <Field label="超时（秒）"><div>{numInput(["ai", "timeout"], { placeholder: "1800" })}</div></Field>
              <Field label="单次请求超时（秒）">
                <div>{numInput(["ai", "request_timeout"], { placeholder: "600" })}</div>
              </Field>
              <Field label="最大输出 tokens">
                <div>{numInput(["ai", "max_tokens"], { placeholder: "32768" })}</div>
              </Field>
              <Field label="温度" hint="0-2">
                <div>{numInput(["ai", "temperature"], { step: "0.1", placeholder: "0.3" })}</div>
              </Field>
              <div className="flex items-end">
                <Toggle label="流式输出（实时进度）"
                  checked={Boolean(get(["ai", "stream"], true))}
                  onChange={(v) => set(["ai", "stream"], v)} />
              </div>
            </div>
          </Section>

          <Section
            title="搜索服务"
            action={(
              <TestButton
                id="search"
                label="测试搜索"
                path="/api/config/test-search"
                configPatch={{ search: pick(parsed?.search, SEARCH_KEYS) }}
                envName={searchEnv}
              />
            )}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="服务商">
                <div>{selectInput(["search", "provider"], ["parallel", "tavily", "brave", "serper", "duckduckgo"])}</div>
              </Field>
              <Field label="模式">
                <div>{selectInput(["search", "mode"], ["advanced", "basic", "fast", "turbo"])}</div>
              </Field>
              <Field label="密钥环境变量" hint="当前服务商">
                <div>{textInput(["search", "api_key_env"], "PARALLEL_API_KEY", true)}</div>
              </Field>
              <Field label="每次结果数">
                <div>{numInput(["search", "max_results"], { placeholder: "10" })}</div>
              </Field>
              <Field label="超时（秒）">
                <div>{numInput(["search", "timeout"], { placeholder: "60" })}</div>
              </Field>
              <Field label="兜底服务商" hint="主服务失败时使用">
                <div>{textInput(["search", "fallback_provider"], "duckduckgo")}</div>
              </Field>
            </div>
            {testResults.search?.sample_url && (
              <div className="text-[11px] text-text-muted truncate">
                示例结果：{testResults.search.sample_url}
              </div>
            )}
          </Section>

          <Section
            title="语义检索（Embedding）"
            action={(
              <TestButton
                id="embedding"
                label="测试 Embedding"
                path="/api/config/test-embedding"
                configPatch={{ ai: pick(parsed?.ai, EMBED_KEYS) }}
                envName={embedEnv || undefined}
              />
            )}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Embedding 模型" hint="留空 = 仅关键词检索">
                <div>{textInput(["ai", "embedding_model"], "bge-m3:latest", true)}</div>
              </Field>
              <Field label="接口地址" hint="可与 LLM 不同">
                <div>{textInput(["ai", "embedding_base_url"], "http://localhost:11434/v1", true)}</div>
              </Field>
              <Field label="密钥环境变量" hint="本地 Ollama 留空">
                <div>{textInput(["ai", "embedding_api_key_env"], "", true)}</div>
              </Field>
            </div>
          </Section>

          <Section title="研究 Agent">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="运行模式" hint="auto 自动回退">
                <div>{selectInput(["research", "mode"], ["auto", "agent", "pipeline"])}</div>
              </Field>
              <Field label="最大工具轮数">
                <div>{numInput(["research", "max_rounds"], { placeholder: "12" })}</div>
              </Field>
              <Field label="最大搜索次数">
                <div>{numInput(["research", "max_searches"], { placeholder: "12" })}</div>
              </Field>
            </div>
          </Section>

          <Section title="预算护栏">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="月度上限（USD）" hint="0 = 不限制">
                <div>{numInput(["budget", "monthly_usd_limit"], { step: "0.5", placeholder: "0" })}</div>
              </Field>
              <Field label="预警比例" hint="0-1">
                <div>{numInput(["budget", "warn_ratio"], { step: "0.05", placeholder: "0.8" })}</div>
              </Field>
              <div className="flex items-end">
                <Toggle label="超限时阻止轮次运行"
                  checked={Boolean(get(["budget", "block_pipeline"], true))}
                  onChange={(v) => set(["budget", "block_pipeline"], v)} />
              </div>
            </div>
          </Section>

          <Section title="通知">
            <Toggle label="启用通知"
              checked={Boolean(get(["notifications", "enabled"], false))}
              onChange={(v) => set(["notifications", "enabled"], v)} />
            <Field label="通知事件" hint="不选则使用默认（失败/取消/轮次完成）">
              <div className="flex flex-wrap gap-3 pt-1">
                {NOTIFY_EVENTS.map(([key, label]) => (
                  <label key={key} className="flex items-center gap-1.5 text-xs cursor-pointer">
                    <input
                      type="checkbox"
                      checked={notifyOn.includes(key)}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...notifyOn, key]
                          : notifyOn.filter((k) => k !== key);
                        set(["notifications", "notify_on"], next);
                      }}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </Field>
            <div className="border-t border-border pt-2 space-y-2">
              <Toggle label="轮次完成时推送内容摘要"
                checked={Boolean(get(["notifications", "digest", "enabled"], true))}
                onChange={(v) => set(["notifications", "digest", "enabled"], v)} />
              <Field label="摘要中的行动条数">
                <div className="max-w-[160px]">
                  {numInput(["notifications", "digest", "max_actions"], { placeholder: "5" })}
                </div>
              </Field>
            </div>
            <div className="text-[11px] text-text-muted">
              渠道与 webhook（企业微信/飞书/PushPlus/Server酱/邮件）请在「YAML（高级）」中配置，密钥建议用 *_url_env 引用。
            </div>
          </Section>

          <Section title="报告清理（可选）">
            <Toggle label="启用生成后清理（extraction）"
              checked={Boolean(get(["extraction", "enabled"], false))}
              onChange={(v) => set(["extraction", "enabled"], v)} />
            <div className="text-[11px] text-text-muted">
              使用较便宜的模型对报告做一次格式清理；一般不需要开启。
            </div>
          </Section>
        </div>
      )}
    </div>
  );
}
