import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getRounds, getReportRaw, runRound, cancelRound, retryRound, type Round } from "@/api";
import { RoundDetail } from "@/components/RoundDetail";
import { useAuthStore } from "@/lib/auth-store";
import { cn, formatTokens } from "@/lib/utils";
import { RefreshCw, Play, Square } from "lucide-react";

const PIPELINE_DIR = "practical_ai_intelligence";

const STAGE_STYLES: Record<string, string> = {
  done: "border-success/60 text-success",
  success: "border-success/60 text-success",
  running: "border-accent text-accent animate-pulse",
  queued: "border-accent/40 text-text-muted",
  pending: "border-border text-text-muted",
  missing: "border-border text-text-muted/50",
  failed: "border-danger/60 text-danger",
  cancelled: "border-warning/60 text-warning",
  skipped: "border-warning/60 text-warning",
  stale: "border-warning/60 text-warning animate-pulse",
};

function stageStyle(status: string): string {
  return STAGE_STYLES[status] || "border-border text-text-muted";
}

export function RoundsPage() {
  const navigate = useNavigate();
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  const [rounds, setRounds] = useState<Round[]>([]);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [compareDate, setCompareDate] = useState<string | null>(null);
  const [detailDate, setDetailDate] = useState<string | null>(null);
  const timerRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getRounds(30);
      setRounds(data.rounds);
      setRunning(data.running);
      setError("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while a round is running
  useEffect(() => {
    if (timerRef.current) window.clearInterval(timerRef.current);
    if (running) {
      timerRef.current = window.setInterval(load, 5000);
    }
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current);
    };
  }, [running, load]);

  const handleRun = async () => {
    if (!window.confirm("运行今日情报轮次（10 个阶段，可能耗时 30-60 分钟）？")) return;
    setBusy(true);
    setError("");
    try {
      await runRound(3);
      setRunning(true);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "启动失败");
    } finally {
      setBusy(false);
    }
  };

  const handleRetry = async () => {
    const ok = window.confirm(
      "补跑未完成/失败的阶段？\n\n" +
      "依赖这些阶段的下游产物（P7/P8/P9）会被标记为过期并一并重跑，" +
      "避免综合结论沿用旧的上游文档。"
    );
    if (!ok) return;
    setBusy(true);
    setError("");
    try {
      const result = await retryRound(3, true);
      const invalidated = result?.invalidated_stages ?? [];
      if (invalidated.length > 0) {
        setNotice(
          `已重新运行 ${(result?.rerun_stages?.length ?? 0) + invalidated.length} 个阶段` +
          `（其中 ${invalidated.length} 个下游阶段因上游重跑而失效）`
        );
      }
      setRunning(true);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "补跑失败");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!window.confirm("取消当前轮次？正在运行的阶段会被停止。")) return;
    setBusy(true);
    try {
      await cancelRound();
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "取消失败");
    } finally {
      setBusy(false);
    }
  };

  const downloadArtifact = async (date: string, name: string) => {
    setError("");
    try {
      const data = await getReportRaw(`${PIPELINE_DIR}/${date}/${name}`);
      const blob = new Blob([data.content], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${date}_${name}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "下载失败");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">情报轮次</h1>
        {running && (
          <span className="inline-flex items-center gap-1.5 text-xs text-accent">
            <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
            运行中
          </span>
        )}
        <button onClick={load} className="btn text-xs" title="刷新">
          <RefreshCw className="w-3.5 h-3.5" />
          刷新
        </button>
        {canEdit && (running ? (
          <button onClick={handleCancel} disabled={busy} className="btn btn-danger text-xs">
            <Square className="w-3 h-3" />
            取消轮次
          </button>
        ) : (
          <>
            {rounds[0] && rounds[0].done < rounds[0].total && (
              <button onClick={handleRetry} disabled={busy} className="btn text-xs">
                <RefreshCw className="w-3 h-3" />
                补跑未完成阶段
              </button>
            )}
            <button onClick={handleRun} disabled={busy} className="btn btn-primary text-xs">
              <Play className="w-3 h-3" />
              {busy ? "启动中..." : "运行今日轮次"}
            </button>
          </>
        ))}
      </div>

      {error && (
        <div className="text-sm text-danger bg-red-900/20 px-3 py-2 rounded-md">{error}</div>
      )}

      {notice && (
        <div className="text-sm text-accent bg-accent/10 px-3 py-2 rounded-md">{notice}</div>
      )}

      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : rounds.length === 0 ? (
        <div className="card p-6 text-center text-text-muted text-sm">
          暂无轮次记录。点击「运行今日轮次」开始。
        </div>
      ) : (
        <div className="space-y-3">
          {rounds.map((round, roundIndex) => {
            const prevRound = rounds[roundIndex + 1];
            const liveStages: Record<string, string> = {};
            for (const s of round.live?.stages || []) liveStages[s.key] = s.status;
            const isLiveRound = round.live?.status === "running";
            return (
              <div key={round.date} className={cn("card", isLiveRound && "border-accent/50")}>
                <div className="px-4 py-2.5 border-b border-border flex items-center gap-3 text-sm">
                  <span className="font-mono">{round.date}</span>
                  <span className="text-text-muted text-xs">
                    {round.done}/{round.total} 完成
                  </span>
                  <div className="h-1.5 flex-1 max-w-[200px] bg-bg-hover rounded-full overflow-hidden">
                    <div
                      className="h-full bg-success rounded-full transition-all"
                      style={{ width: `${(round.done / round.total) * 100}%` }}
                    />
                  </div>
                  {round.live && (
                    <span className={cn(
                      "badge border text-xs",
                      round.live.status === "running" ? "border-accent text-accent" :
                      round.live.status === "success" ? "border-success/60 text-success" :
                      round.live.status === "partial" ? "border-warning/60 text-warning" :
                      "border-danger/60 text-danger"
                    )}>
                      {round.live.status}
                    </span>
                  )}
                  {round.live?.trigger && (
                    <span className="text-xs text-text-muted">by {round.live.trigger}</span>
                  )}
                  {round.tokens_total ? (
                    <span className="text-xs text-text-muted">{formatTokens(round.tokens_total)} tok</span>
                  ) : null}
                  {(round.artifacts || []).map((a) => (
                    <button
                      key={a.name}
                      onClick={() => downloadArtifact(round.date, a.name)}
                      className="text-xs px-1.5 py-0.5 rounded border border-border text-text-muted hover:text-text hover:bg-bg-hover transition font-mono"
                      title={`下载 ${a.name}（${(a.size / 1024).toFixed(1)} KB）`}
                    >
                      {a.name.replace(/\.json$/, "")}
                    </button>
                  ))}
                  <button
                    onClick={() => setDetailDate(round.date)}
                    className="text-xs text-text-muted hover:text-text underline-offset-2 hover:underline"
                  >
                    详情
                  </button>
                  {roundIndex < rounds.length - 1 && (
                    <button
                      onClick={() => setCompareDate(compareDate === round.date ? null : round.date)}
                      className="text-xs text-text-muted hover:text-text underline-offset-2 hover:underline"
                    >
                      {compareDate === round.date ? "收起对比" : "对比上一轮"}
                    </button>
                  )}
                </div>
                <div className="px-4 py-3 flex flex-wrap gap-1.5">
                  {round.stages.map((stage) => {
                    const liveStatus = liveStages[stage.key];
                    const status = liveStatus && liveStatus !== "pending"
                      ? liveStatus
                      : stage.exists ? "done" : stage.status;
                    const clickable = stage.exists;
                    const running = status === "running";
                    return (
                      <button
                        key={stage.key}
                        disabled={!clickable && !running}
                        onClick={() => {
                          if (running) {
                            navigate(`/jobs/${encodeURIComponent(`${PIPELINE_DIR}/${stage.key}`)}`);
                          } else if (clickable) {
                            navigate(`/reports/view?path=${encodeURIComponent(stage.file)}`);
                          }
                        }}
                        className={cn(
                          "px-2 py-1 text-xs rounded-md border transition",
                          stageStyle(status),
                          (clickable || running) && "hover:bg-bg-hover cursor-pointer",
                          !clickable && !running && "cursor-default"
                        )}
                        title={
                          running ? "查看实时日志"
                            : clickable ? `打开报告（${(stage.size / 1024).toFixed(1)} KB）`
                            : "暂无产出"
                        }
                      >
                        {stage.label}
                      </button>
                    );
                  })}
                </div>

                {compareDate === round.date && prevRound && (
                  <div className="px-4 pb-3 overflow-x-auto border-t border-border/50">
                    <table className="min-w-full text-xs mt-2">
                      <thead>
                        <tr className="text-text-muted">
                          <th className="text-left py-1 pr-3 font-medium">阶段</th>
                          <th className="text-right py-1 px-2 font-medium">{prevRound.date}</th>
                          <th className="text-right py-1 px-2 font-medium">{round.date}</th>
                          <th className="text-right py-1 px-2 font-medium">Δ 大小</th>
                          <th className="text-right py-1 px-2 font-medium">Δ tokens</th>
                        </tr>
                      </thead>
                      <tbody>
                        {round.stages.map((stage, idx) => {
                          const prev = prevRound.stages[idx];
                          const dSize = (stage.size || 0) - (prev?.size || 0);
                          const dTok = (stage.tokens || 0) - (prev?.tokens || 0);
                          const fmtDelta = (n: number, fmt: (v: number) => string) =>
                            n === 0 ? "—" : `${n > 0 ? "+" : "-"}${fmt(Math.abs(n))}`;
                          return (
                            <tr key={stage.key} className="border-t border-border/50">
                              <td className="py-1 pr-3">{stage.label}</td>
                              <td className="py-1 px-2 text-right text-text-muted">
                                {prev?.exists ? `${(prev.size / 1024).toFixed(1)} KB` : "—"}
                              </td>
                              <td className="py-1 px-2 text-right text-text-muted">
                                {stage.exists ? `${(stage.size / 1024).toFixed(1)} KB` : "—"}
                              </td>
                              <td className={cn("py-1 px-2 text-right",
                                dSize !== 0 && (dSize > 0 ? "text-success" : "text-warning"))}>
                                {fmtDelta(dSize, (v) => `${(v / 1024).toFixed(1)} KB`)}
                              </td>
                              <td className={cn("py-1 px-2 text-right",
                                dTok !== 0 && (dTok > 0 ? "text-warning" : "text-success"))}>
                                {fmtDelta(dTok, formatTokens)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {detailDate && (
        <RoundDetail date={detailDate} onClose={() => setDetailDate(null)} />
      )}
    </div>
  );
}
