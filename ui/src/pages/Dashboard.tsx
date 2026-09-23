import { useEffect, useState } from "react";
import { listJobs, getUsage, getReportStats, getRatings, getHealth, type UsageSummary, type RatingSummary, type HealthReport } from "@/api";
import type { JobSummary } from "@/api/types";
import { cn, timeAgo, formatTokens } from "@/lib/utils";

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-text-muted">未运行</span>;
  const colors: Record<string, string> = {
    success: "bg-green-900/40 text-green-400 border-green-800",
    failed: "bg-red-900/40 text-red-400 border-red-800",
    skipped: "bg-yellow-900/40 text-yellow-400 border-yellow-800",
    cancelled: "bg-yellow-900/40 text-yellow-400 border-yellow-800",
  };
  return (
    <span className={cn("badge border", colors[status] || "bg-gray-800 text-gray-400")}>
      {status}
    </span>
  );
}

export function DashboardPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [reportStats, setReportStats] = useState<{ total: number; total_size_bytes: number; categories: number } | null>(null);
  const [ratings, setRatings] = useState<RatingSummary | null>(null);
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthOpen, setHealthOpen] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listJobs()
      .then(setJobs)
      .finally(() => setLoading(false));
    getUsage(30).then(setUsage).catch(() => setUsage(null));
    getReportStats().then(setReportStats).catch(() => setReportStats(null));
    getRatings().then(setRatings).catch(() => setRatings(null));
    getHealth().then(setHealth).catch(() => setHealth(null));
  }, []);

  const total = jobs.length;
  const enabled = jobs.filter((j) => j.enabled).length;
  const running = jobs.filter((j) => j.is_running).length;
  const lastFailed = jobs.filter((j) => j.state?.last_status === "failed").length;

  const stats = [
    { label: "Total", value: total, color: "" },
    { label: "已启用", value: enabled, color: "text-accent" },
    { label: "运行中", value: running, color: running > 0 ? "text-accent" : "" },
    { label: "上次失败", value: lastFailed, color: lastFailed > 0 ? "text-danger" : "" },
  ];

  const recent = jobs
    .filter((j) => j.state?.last_run_at)
    .sort((a, b) => (b.state?.last_run_at || "").localeCompare(a.state?.last_run_at || ""))
    .slice(0, 10);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <h1 className="text-lg font-semibold flex-1">总览</h1>
        {health && (
          <button
            onClick={() => setHealthOpen((v) => !v)}
            className="text-xs"
            title={health.checks.map((c) => `${c.name}: ${c.status} — ${c.detail}`).join("\n")}
          >
            <span
              className={cn(
                "badge border",
                health.status === "ok"
                  ? "bg-green-900/40 text-green-400 border-green-800"
                  : health.status === "warn"
                    ? "bg-yellow-900/40 text-yellow-400 border-yellow-800"
                    : "bg-red-900/40 text-red-400 border-red-800"
              )}
            >
              系统 {health.status === "ok" ? "正常" : health.status === "warn" ? `注意 ${health.warnings.length}` : `异常 ${health.errors.length}`}
            </span>
          </button>
        )}
      </div>

      {health && healthOpen && (
        <div className="card p-3 space-y-1.5">
          <div className="text-xs text-text-muted">
            检查于 {health.checked_at}（不含索引/向量/轮次等深度检查）
          </div>
          <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1">
            {health.checks.map((c) => (
              <div key={c.name} className="flex items-start gap-2 text-xs">
                <span
                  className={cn(
                    "mt-0.5 w-1.5 h-1.5 rounded-full shrink-0",
                    c.status === "ok" ? "bg-success" : c.status === "warn" ? "bg-warning" : "bg-danger"
                  )}
                />
                <span className="font-mono">{c.name}</span>
                <span className="text-text-muted truncate">{c.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div key={s.label} className="card p-4">
            <div className="text-xs text-text-muted">{s.label}</div>
            <div className={cn("text-2xl font-bold mt-1", s.color)}>
              {loading ? "..." : s.value}
            </div>
          </div>
        ))}
      </div>

      {usage?.budget && usage.budget.limit > 0 && (
        <div className={cn("card px-4 py-3 space-y-2",
          usage.budget.exceeded && "border-danger/60")}>
          <div className="flex items-center gap-2 text-xs">
            <span className="text-text-muted">月度预算</span>
            <span className={cn("font-mono",
              usage.budget.exceeded ? "text-danger" :
              usage.budget.warn ? "text-warning" : "text-text")}>
              ${usage.budget.spent.toFixed(2)} / ${usage.budget.limit.toFixed(2)}
            </span>
            {usage.budget.exceeded && (
              <span className="text-danger">已超限{usage.budget.block_pipeline ? "，轮次已被阻止" : ""}</span>
            )}
            {!usage.budget.exceeded && usage.budget.warn && (
              <span className="text-warning">接近上限</span>
            )}
          </div>
          <div className="h-1.5 w-full bg-bg-hover rounded-full overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all",
                usage.budget.exceeded ? "bg-danger" :
                usage.budget.warn ? "bg-warning" : "bg-accent")}
              style={{ width: `${Math.min(100, usage.budget.ratio * 100).toFixed(1)}%` }}
            />
          </div>
        </div>
      )}

      {reportStats && (
        <div className="card px-4 py-2.5 text-xs text-text-muted">
          报告库：{reportStats.total} 篇 · {(reportStats.total_size_bytes / 1024 / 1024).toFixed(1)} MB · {reportStats.categories} 个分类
          {ratings && ratings.count > 0 && ratings.average != null && (
            <span> · 平均评分 <span className="text-warning">★ {ratings.average.toFixed(1)}</span>（{ratings.count} 篇）</span>
          )}
        </div>
      )}

      {/* Usage (last 30 days) */}
      {usage && (usage.totals?.runs ?? 0) > 0 && (
        <div className="card">
          <div className="px-4 py-3 border-b border-border font-medium text-sm flex items-center gap-2">
            用量（近 30 天）
            {usage.estimated_cost_usd != null && (
              <span className="text-xs text-text-muted font-normal">
                估算成本 ${usage.estimated_cost_usd.toFixed(2)}
              </span>
            )}
          </div>
          <div className="px-4 py-3 grid grid-cols-2 lg:grid-cols-4 gap-3 text-sm">
            <div>
              <div className="text-xs text-text-muted">运行次数</div>
              <div className="font-mono">{usage.totals.runs_with_usage} 次（含用量）</div>
            </div>
            <div>
              <div className="text-xs text-text-muted">总 tokens</div>
              <div className="font-mono">{formatTokens(usage.totals.total_tokens)}</div>
            </div>
            <div>
              <div className="text-xs text-text-muted">输入 / 输出</div>
              <div className="font-mono">
                {formatTokens(usage.totals.prompt_tokens)} / {formatTokens(usage.totals.completion_tokens)}
              </div>
            </div>
            <div>
              <div className="text-xs text-text-muted">搜索次数</div>
              <div className="font-mono">{usage.totals.searches}</div>
            </div>
          </div>
          {usage.per_model.length > 0 && (
            <div className="px-4 pb-3 text-xs text-text-muted space-y-0.5">
              {usage.per_model.slice(0, 4).map((m) => (
                <div key={m.model} className="flex gap-2">
                  <span className="font-mono">{m.model}</span>
                  <span>· {m.runs} 次 · {formatTokens(m.total_tokens)} tokens</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="px-4 py-3 border-b border-border font-medium text-sm">
          最近运行
        </div>
        {recent.length === 0 ? (
          <div className="p-4 text-sm text-text-muted">暂无运行记录</div>
        ) : (
          <div className="divide-y divide-border text-sm">
            {recent.map((j) => (
              <div key={j.name} className="px-4 py-2.5 flex items-center gap-3">
                <StatusBadge status={j.state?.last_status || null} />
                <span className="font-mono text-xs flex-1 truncate">{j.name}</span>
                {j.state?.last_usage?.total_tokens != null && (
                  <span className="text-text-muted text-xs">
                    {formatTokens(j.state.last_usage.total_tokens)} tok
                  </span>
                )}
                {j.state?.last_duration_seconds != null && (
                  <span className="text-text-muted text-xs">
                    {Math.round(j.state.last_duration_seconds)}s
                  </span>
                )}
                <span className="text-text-muted text-xs" title={j.state?.last_run_at || ""}>
                  {timeAgo(j.state?.last_run_at)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
