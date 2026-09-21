import { useEffect, useState, useCallback, useRef, lazy, Suspense } from "react";
import { useParams, Link } from "react-router-dom";
import { getJob, updateJobYaml, runJob, cancelJob, getJobHistory } from "@/api";
import { ApiError } from "@/api/client";
import type { JobDetail as JobDetailType } from "@/api/types";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";
import { ArrowLeft, Play, Square } from "lucide-react";
import { useLogStream, type LogEvent } from "@/hooks/useLogStream";

const YamlEditor = lazy(() => import("@/components/YamlEditor").then(m => ({ default: m.YamlEditor })));

type Tab = "overview" | "yaml" | "logs" | "history";

export function JobDetailPage() {
  const { name } = useParams<{ name: string }>();
  const user = useAuthStore((s) => s.user);
  const [job, setJob] = useState<JobDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("overview");

  // Run history
  const [history, setHistory] = useState<any[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Run state
  const [runBusy, setRunBusy] = useState(false);
  const [runError, setRunError] = useState("");
  const [showLogs, setShowLogs] = useState(false);

  // YAML editing state
  const [editYaml, setEditYaml] = useState("");
  const [originalYaml, setOriginalYaml] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [lastSaveResult, setLastSaveResult] = useState("");

  const canEdit = user?.role === "editor" || user?.role === "admin";
  const hasChanges = editYaml !== originalYaml;

  // SSE log stream (enabled when we have a running task and are on logs tab, or after trigger)
  const decodedName = name ? decodeURIComponent(name) : "";
  const { events: logEvents, streamStatus, isRunning: sseRunning, clear: clearLogs, reconnect: reconnectLogs, latestStatus } = useLogStream({
    jobName: decodedName,
    enabled: showLogs,
  });

  const fetchJob = useCallback(async () => {
    if (!name) return;
    setLoading(true);
    setError("");
    try {
      const data = await getJob(decodeURIComponent(name));
      setJob(data);
      setEditYaml(data.yaml_content);
      setOriginalYaml(data.yaml_content);
      setWarnings([]);
      setSaveError("");
      setLastSaveResult("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [name]);

  useEffect(() => {
    fetchJob();
  }, [fetchJob]);

  const decodedNameForHistory = name ? decodeURIComponent(name) : "";
  useEffect(() => {
    if (tab !== "history" || !decodedNameForHistory) return;
    setHistoryLoading(true);
    getJobHistory(decodedNameForHistory, 50)
      .then((rows) => setHistory(Array.isArray(rows) ? rows : []))
      .catch(() => setHistory([]))
      .finally(() => setHistoryLoading(false));
  }, [tab, decodedNameForHistory]);

  // When SSE finishes, refetch job to get updated state/output
  useEffect(() => {
    if (latestStatus && !sseRunning) {
      fetchJob();
      setShowLogs(false);
    }
  }, [latestStatus, sseRunning, fetchJob]);

  // Sync when tab switches
  useEffect(() => {
    if (tab === "yaml" && job) {
      setWarnings([]);
      setSaveError("");
      setLastSaveResult("");
    }
  }, [tab, job]);

  const handleRun = async () => {
    if (!name || runBusy) return;
    setRunBusy(true);
    setRunError("");
    setShowLogs(true);
    setTab("logs");
    clearLogs();
    try {
      await runJob(decodeURIComponent(name));
    } catch (err: unknown) {
      if (err instanceof ApiError && err.code === "already_running") {
        setRunError("Job 正在运行中");
      } else {
        setRunError(err instanceof Error ? err.message : "启动失败");
      }
      setShowLogs(false);
    } finally {
      setRunBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!name) return;
    try {
      await cancelJob(decodeURIComponent(name));
    } catch {
      // ignore
    }
  };

  const handleSave = async () => {
    if (!name || !job || !hasChanges) return;
    setSaving(true);
    setSaveError("");
    setWarnings([]);
    setLastSaveResult("");
    try {
      const result = await updateJobYaml(
        decodeURIComponent(name),
        editYaml,
        job.yaml_mtime
      );
      setOriginalYaml(editYaml);
      setWarnings(result.warnings || []);
      setLastSaveResult("保存成功");
      fetchJob();
    } catch (err: unknown) {
      if (err instanceof ApiError && err.code === "conflict") {
        setSaveError(`外部修改冲突: ${err.message}`);
      } else {
        setSaveError(err instanceof Error ? err.message : "保存失败");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setEditYaml(originalYaml);
    setWarnings([]);
    setSaveError("");
    setLastSaveResult("");
  };

  if (loading) {
    return <div className="text-text-muted">加载中...</div>;
  }

  if (error || !job) {
    return (
      <div className="space-y-4">
        <Link to="/jobs" className="btn">
          <ArrowLeft className="w-4 h-4" />
          返回
        </Link>
        <div className="card p-4 text-danger">{error || "Job not found"}</div>
      </div>
    );
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "概览" },
    { key: "yaml", label: "YAML 配置" },
    { key: "logs", label: "日志" },
    { key: "history", label: "运行历史" },
  ];

  const statusColor: Record<string, string> = {
    success: "text-success",
    failed: "text-danger",
    cancelled: "text-warning",
    skipped: "text-warning",
  };

  const isRunning = job.is_running || sseRunning;

  return (
    <div className="space-y-4">
      {/* Breadcrumb + actions */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/jobs" className="text-text-muted hover:text-text">
          <ArrowLeft className="w-4 h-4 inline mr-1" />
          Jobs
        </Link>
        <span className="text-text-muted">/</span>
        <span className="font-mono flex-1">{job.name}</span>

        <div className="flex items-center gap-2">
          {runError && (
            <span className="text-xs text-danger">{runError}</span>
          )}
          {canEdit && isRunning && (
            <button onClick={handleCancel} className="btn btn-danger text-xs" title="取消运行">
              <Square className="w-3 h-3" />
              停止
            </button>
          )}
          {canEdit && !isRunning && (
            <button onClick={handleRun} disabled={runBusy} className="btn btn-primary text-xs" title="运行此 Job">
              <Play className="w-3 h-3" />
              {runBusy ? "启动中..." : "运行"}
            </button>
          )}
          {isRunning && (
            <span className="inline-flex items-center gap-1.5 text-xs text-accent">
              <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
              运行中
            </span>
          )}
        </div>
      </div>

      {/* Tabs */}
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

      {/* === Overview tab === */}
      {tab === "overview" && (
        <div className="space-y-4">
          <div className="card divide-y divide-border">
            <div className="px-4 py-3 flex items-center gap-4 text-sm">
              <span className="text-text-muted w-20 shrink-0">状态</span>
              <span className={cn(statusColor[job.state?.last_status || ""] || "text-text-muted")}>
                {isRunning ? "运行中" : (job.state?.last_status || "未运行")}
              </span>
            </div>
            <div className="px-4 py-3 flex items-center gap-4 text-sm">
              <span className="text-text-muted w-20 shrink-0">启用</span>
              <span>{job.enabled ? "是" : "否"}</span>
            </div>
            {job.description && (
              <div className="px-4 py-3 text-sm">
                <span className="text-text-muted block mb-1">描述</span>
                <span>{job.description}</span>
              </div>
            )}
            {job.keywords.length > 0 && (
              <div className="px-4 py-3 text-sm">
                <span className="text-text-muted block mb-1">关键词</span>
                <div className="flex flex-wrap gap-1.5">
                  {job.keywords.map((kw: string) => (
                    <span key={kw} className="badge bg-bg-hover border border-border">
                      {kw}
                    </span>
                  ))}
                </div>
              </div>
            )}
            <div className="px-4 py-3 text-sm">
              <span className="text-text-muted block mb-1">输出路径模板</span>
              <code className="text-xs bg-bg-hover px-1.5 py-0.5 rounded">
                {job.output_template || "(默认)"}
              </code>
            </div>
            {job.state?.last_run_at && (
              <>
                <div className="px-4 py-3 text-sm">
                  <span className="text-text-muted block mb-1">上次运行</span>
                  <span>{job.state.last_run_at}</span>
                </div>
                {job.state.last_duration_seconds != null && (
                  <div className="px-4 py-3 text-sm">
                    <span className="text-text-muted block mb-1">耗时</span>
                    <span>{Math.round(job.state.last_duration_seconds)}s</span>
                  </div>
                )}
                {job.state.last_usage && (job.state.last_usage.total_tokens ?? 0) > 0 && (
                  <div className="px-4 py-3 text-sm">
                    <span className="text-text-muted block mb-1">上次用量</span>
                    <span className="font-mono text-xs">
                      {job.state.last_usage.model} ·{" "}
                      {(job.state.last_usage.total_tokens ?? 0).toLocaleString()} tokens
                      {job.state.last_usage.searches != null &&
                        ` · ${job.state.last_usage.searches} 次搜索`}
                    </span>
                  </div>
                )}
                {job.state.last_output && (
                  <div className="px-4 py-3 text-sm">
                    <span className="text-text-muted block mb-1">输出文件</span>
                    <code className="text-xs bg-bg-hover px-1.5 py-0.5 rounded">
                      {job.state.last_output}
                    </code>
                  </div>
                )}
                {job.state.last_error && (
                  <div className="px-4 py-3 text-sm">
                    <span className="text-text-muted block mb-1">错误</span>
                    <code className="text-xs text-danger bg-red-900/20 px-1.5 py-0.5 rounded block mt-1 whitespace-pre-wrap">
                      {job.state.last_error}
                    </code>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}

      {/* === YAML tab === */}
      {tab === "yaml" && (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            {canEdit && (
              <>
                <button onClick={handleSave} disabled={saving || !hasChanges} className="btn btn-primary">
                  {saving ? "保存中..." : "保存"}
                </button>
                <button onClick={handleReset} disabled={!hasChanges} className="btn">
                  还原
                </button>
                {hasChanges && <span className="text-xs text-warning">有未保存的修改</span>}
              </>
            )}
            {!canEdit && <span className="text-xs text-text-muted">(只读模式)</span>}
          </div>

          {saveError && (
            <div className="text-sm text-danger bg-red-900/20 px-3 py-2 rounded-md">{saveError}</div>
          )}
          {lastSaveResult && (
            <div className="text-sm text-success bg-green-900/20 px-3 py-2 rounded-md">{lastSaveResult}</div>
          )}
          {warnings.length > 0 && (
            <div className="text-sm text-warning bg-yellow-900/20 px-3 py-2 rounded-md space-y-1">
              {warnings.map((w: string, i: number) => (
                <div key={i}>⚠ {w}</div>
              ))}
            </div>
          )}

          <Suspense fallback={<div className="card p-6 text-center text-text-muted text-sm">加载编辑器...</div>}>
            <YamlEditor
              value={editYaml}
              onChange={canEdit ? setEditYaml : undefined}
              readOnly={!canEdit}
              height="calc(100vh - 320px)"
            />
          </Suspense>
        </div>
      )}

      {/* === History tab === */}
      {tab === "history" && (
        <div className="space-y-3">
          {historyLoading ? (
            <div className="text-sm text-text-muted">加载中...</div>
          ) : history.length === 0 ? (
            <div className="card p-6 text-center text-text-muted text-sm">暂无运行历史</div>
          ) : (
            <div className="card overflow-x-auto">
              <table className="min-w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-text-muted">
                    <th className="text-left px-3 py-2 font-medium">时间</th>
                    <th className="text-left px-3 py-2 font-medium">状态</th>
                    <th className="text-left px-3 py-2 font-medium">耗时</th>
                    <th className="text-left px-3 py-2 font-medium">tokens</th>
                    <th className="text-left px-3 py-2 font-medium">输出</th>
                    <th className="text-left px-3 py-2 font-medium">错误</th>
                  </tr>
                </thead>
                <tbody>
                  {history.map((h, i) => (
                    <tr key={i} className="border-b border-border hover:bg-bg-hover">
                      <td className="px-3 py-2 whitespace-nowrap">
                        {h.ts?.slice(0, 19)?.replace("T", " ") || "-"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span className={cn("badge",
                          h.status === "success" ? "text-success" :
                          h.status === "failed" ? "text-danger" : "text-warning"
                        )}>{h.status}</span>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {h.duration_seconds != null ? `${Math.round(h.duration_seconds)}s` : "-"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap font-mono">
                        {h.usage?.total_tokens != null ? h.usage.total_tokens.toLocaleString() : "-"}
                      </td>
                      <td className="px-3 py-2">
                        {h.output_path ? (
                          <button
                            className="text-accent hover:underline truncate max-w-[220px]"
                            onClick={() => {
                              const rel = String(h.output_path);
                              const outIdx = rel.indexOf("output/");
                              const path = outIdx >= 0 ? rel.slice(outIdx + "output/".length) : rel;
                              window.location.href = `/reports/view?path=${encodeURIComponent(path)}`;
                            }}
                          >
                            {String(h.output_path).split("/").pop()}
                          </button>
                        ) : "-"}
                      </td>
                      <td className="px-3 py-2 text-danger truncate max-w-[240px]" title={h.error || ""}>
                        {h.error ? String(h.error).slice(0, 80) : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* === Logs tab === */}
      {tab === "logs" && (
        <LogViewer
          events={logEvents}
          streamStatus={streamStatus}
          autoScroll={true}
          onRetry={reconnectLogs}
        />
      )}
    </div>
  );
}

// --- LogViewer sub-component ---

function LogViewer({
  events,
  streamStatus,
  autoScroll,
  onRetry,
}: {
  events: LogEvent[];
  streamStatus: string;
  autoScroll: boolean;
  onRetry?: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, autoScroll]);

  if (streamStatus === "error") {
    return (
      <div className="card p-6 text-center text-sm space-y-2">
        <div className="text-danger">日志流连接失败（任务不存在、服务重启或登录已过期）</div>
        {onRetry && (
          <button onClick={onRetry} className="btn text-xs">
            重新连接
          </button>
        )}
      </div>
    );
  }

  if (events.length === 0 && streamStatus === "connecting") {
    return (
      <div className="card p-6 text-center text-text-muted text-sm">
        <div className="animate-pulse">等待日志...</div>
      </div>
    );
  }

  if (events.length === 0) {
    return (
      <div className="card p-6 text-center text-text-muted text-sm">
        暂无日志。点击"运行"按钮启动 Job。
      </div>
    );
  }

  return (
    <div className="card">
      <div className="px-4 py-2 border-b border-border flex items-center gap-2 text-xs text-text-muted">
        <span className={cn(
          "w-2 h-2 rounded-full",
          streamStatus === "connected" ? "bg-accent animate-pulse" :
          streamStatus === "finished" ? "bg-success" :
          "bg-text-muted"
        )} />
        {streamStatus === "connected" && "实时"}
        {streamStatus === "finished" && "已完成"}
        {streamStatus === "connecting" && "连接中"}
        {streamStatus === "error" && "错误"}
        <span className="ml-auto">{events.length} lines</span>
      </div>
      <div
        className="p-3 text-xs font-mono leading-relaxed max-h-[60vh] overflow-y-auto bg-[#0d1117]"
        role="log"
        aria-live="polite"
      >
        {events.map((ev, i) => {
          if (ev.type === "log") {
            const level = ev.level || "info";
            const color = level === "error" ? "text-danger" : level === "warning" ? "text-warning" : "text-text-muted";
            return (
              <div key={i} className={cn("whitespace-pre-wrap break-all", color)}>
                {ev.message}
              </div>
            );
          }
          if (ev.type === "progress") {
            const eventType = String(ev.event_type || "");
            if (eventType === "phase") {
              return (
                <div key={i} className="text-accent whitespace-pre-wrap break-all">
                  ▶ Phase: {String(ev.phase ?? "")}
                </div>
              );
            }
            if (eventType === "round") {
              return (
                <div key={i} className="text-accent whitespace-pre-wrap break-all">
                  ⟳ Round {String(ev.round ?? "?")}/{String(ev.max_rounds ?? "?")}
                </div>
              );
            }
            if (eventType === "search") {
              const queries = Array.isArray(ev.queries) ? (ev.queries as string[]).join("; ") : "";
              return (
                <div key={i} className="text-text-muted whitespace-pre-wrap break-all">
                  ⌕ Search[{String(ev.provider ?? "")}]: {queries}
                </div>
              );
            }
            if (eventType === "tool") {
              return (
                <div key={i} className="text-text-muted whitespace-pre-wrap break-all">
                  ⚙ Tool: {String(ev.tool ?? "")}
                </div>
              );
            }
            if (eventType === "generating") {
              const chars = Number(ev.chars || 0);
              const thinking = Number(ev.reasoning_chars || 0);
              const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
              return (
                <div key={i} className="text-text-muted/80 whitespace-pre-wrap break-all">
                  ✎ {thinking > 0 ? `thinking ${fmt(thinking)} / ` : ""}
                  writing {fmt(chars)} chars ({String(ev.elapsed ?? "?")}s)
                </div>
              );
            }
            if (eventType === "heartbeat") {
              return (
                <div key={i} className="text-text-muted/60 whitespace-pre-wrap break-all">
                  … working ({String(ev.elapsed ?? "?")}s)
                </div>
              );
            }
            return null;
          }
          if (ev.type === "status") {
            return (
              <div key={i} className={cn(
                "whitespace-pre-wrap break-all font-semibold",
                ev.status === "success" ? "text-success" :
                ev.status === "failed" ? "text-danger" :
                ev.status === "cancelled" ? "text-warning" : "text-text"
              )}>
                ■ Status: {ev.status}
              </div>
            );
          }
          return null;
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
