import { useCallback, useEffect, useState } from "react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { ErrorState } from "@/components/ErrorState";
import { errorMessage, useToast } from "@/lib/toast";
import { AlertTriangle, Clock, Trash2, Play, Pause, HelpCircle, Pencil, RefreshCw, ShieldCheck } from "lucide-react";
import { ScheduleRuns } from "@/components/ScheduleRuns";
import { CronBuilder, scheduleSummary } from "@/components/CronBuilder";

interface ScheduledJob {
  id: string;
  minute: string;
  hour: string;
  day_of_month: string;
  month: string;
  day_of_week: string;
  command: string;
  enabled: boolean;
  backend?: "cron" | "launchd" | "managed";
  job_name?: string;
  timezone?: string;
  missed_policy?: string;
  max_retries?: number;
  editable?: boolean;
  title?: string;
  schedule_label?: string;
  next_runs?: string[];
}
interface Dispatcher { online: boolean; detail: string; age_seconds?: number }
interface SchedulableJob {
  schedule_id?: string;
  name: string;
  label: string;
  description: string;
  enabled: boolean;
  command: string;
}
interface JobScheduleState {
  id: string;
  status: "ok" | "paused" | "overdue" | "unverified" | "no_schedule" | "running" | "queued" | "retry";
  detail: string;
  schedule: string;
  last_ran_at?: string;
}
interface Preview { schedule: string; timezone: string; next_runs: string[]; requestedTimezone?: string }
const EMPTY = { id: "", job_name: "", schedule: "0 8 * * *", command: "", timezone: "local", missed_policy: "latest", max_retries: 2 };
const STATUS = { ok: "有运行证据", paused: "已暂停", overdue: "需要检查", unverified: "待验证", no_schedule: "时间待检查", running: "运行中", queued: "等待执行", retry: "等待重试" };
const expression = (job: ScheduledJob) => [job.minute, job.hour, job.day_of_month, job.month, job.day_of_week].join(" ");
// Preserve server wall time; the browser may be in a different timezone.
const displayTime = (iso?: string) => iso ? iso.slice(0, 16).replace("T", " ") : "—";

export function SchedulerPage() {
  const toast = useToast();
  const [dispatcher, setDispatcher] = useState<Dispatcher | null>(null);
  const [historyId, setHistoryId] = useState("");
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState("");
  const [schedulable, setSchedulable] = useState<SchedulableJob[]>([]);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [states, setStates] = useState<Record<string, JobScheduleState>>({});
  const [jobQuery, setJobQuery] = useState("");
  const [selectedJob, setSelectedJob] = useState("");
  const [timezone, setTimezone] = useState("服务器本地时区");
  const [warnings, setWarnings] = useState<string[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);

  const fetchJobs = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const data = await api<{ jobs: ScheduledJob[]; health?: { jobs: JobScheduleState[] }; timezone?: string; warnings?: string[]; dispatcher?: Dispatcher }>("/api/scheduler");
      setJobs(data.jobs);
      setDispatcher(data.dispatcher || null);
      setStates(Object.fromEntries((data.health?.jobs || []).map(entry => [entry.id, entry])));
      setTimezone(data.timezone || "服务器本地时区");
      setWarnings(data.warnings || []);
    } catch (err) { setError(errorMessage(err, "加载失败")); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void fetchJobs(); }, [fetchJobs]);
  useEffect(() => {
    const timer = window.setInterval(() => { if (!document.hidden && !showForm && !busy) void fetchJobs(true); }, 15000);
    return () => window.clearInterval(timer);
  }, [fetchJobs, showForm, busy]);

  const fetchCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError("");
    try {
      const data = await api<{ jobs: SchedulableJob[] }>("/api/scheduler/jobs");
      setSchedulable(data.jobs || []);
    } catch (err) { setCatalogError(errorMessage(err, "任务列表加载失败")); }
    finally { setCatalogLoading(false); }
  }, []);
  useEffect(() => { if (showForm && !editing) void fetchCatalog(); }, [showForm, editing, fetchCatalog]);

  useEffect(() => {
    if (!showForm) return;
    let active = true;
    const controller = new AbortController();
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    const timer = setTimeout(async () => {
      try {
        const data = await api<Preview>("/api/scheduler/preview", { query: { schedule: form.schedule, timezone: form.timezone }, signal: controller.signal });
        if (active) setPreview({ ...data, requestedTimezone: form.timezone });
      } catch (err) { if (active) setPreviewError(errorMessage(err, "时间预览失败")); }
      finally { if (active) setPreviewLoading(false); }
    }, 300);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [showForm, form.schedule, form.timezone]);

  const openNew = () => {
    setEditing(null); setForm({ ...EMPTY }); setMsg(""); setJobQuery(""); setSelectedJob(""); setPreview(null);
    setShowForm(true);
  };
  const openEdit = (job: ScheduledJob) => {
    setEditing(job.id); setForm({ ...EMPTY, id: job.id, job_name: job.job_name || "", schedule: expression(job), command: job.command, timezone: job.timezone || "local", missed_policy: job.missed_policy || "latest", max_retries: job.max_retries ?? 2 });
    setMsg(""); setPreview(null); setShowForm(true);
  };
  const applyJob = (job: SchedulableJob) => {
    if (job.schedule_id) {
      const plan = jobs.find(j => j.id === job.schedule_id);
      if (plan) openEdit(plan);
      else setMsg("这个任务已有计划，请刷新后编辑现有计划");
      return;
    }
    setSelectedJob(job.name);
    setForm(prev => ({ ...prev, id: "", job_name: job.name, command: job.command }));
  };
  const save = async () => {
    if (busy) return;
    setBusy("save"); setMsg("");
    try {
      await api(editing ? `/api/scheduler/${encodeURIComponent(editing)}` : "/api/scheduler", {
        method: editing ? "PUT" : "POST", body: form,
      });
      toast.success(editing ? "调度已更新" : "调度已创建");
      setShowForm(false); setEditing(null); setForm({ ...EMPTY });
      await fetchJobs();
    } catch (err) { setMsg(errorMessage(err, "保存失败")); }
    finally { setBusy(""); }
  };
  const mutate = async (job: ScheduledJob, remove: boolean) => {
    if (busy) return;
    if (remove && !window.confirm(`删除调度「${job.id}」？研究任务和已有报告会保留。`)) return;
    setBusy(job.id); setMsg("");
    try {
      await api(`/api/scheduler/${encodeURIComponent(job.id)}${remove ? "" : "/toggle"}`, {
        method: remove ? "DELETE" : "PUT", ...(remove ? {} : { body: { enabled: !job.enabled } }),
      });
      toast.success(remove ? "调度已删除" : job.enabled ? "调度已暂停" : "调度已启用");
      await fetchJobs();
    } catch (err) { setMsg(errorMessage(err)); }
    finally { setBusy(""); }
  };
  const startDispatcher = async () => {
    setBusy("host"); setMsg("");
    try { await api("/api/scheduler/dispatcher/start", { method: "POST" }); await fetchJobs(); }
    catch (err) { setMsg(errorMessage(err)); }
    finally { setBusy(""); }
  };
  const legacyEdit = !!editing && jobs.find(j => j.id === editing)?.backend !== "managed";
  const filteredJobs = schedulable.filter(job => `${job.name} ${job.label} ${job.description}`.toLowerCase().includes(jobQuery.trim().toLowerCase()));
  const hasLaunchd = jobs.some(job => job.backend === "launchd");
  const needsAttention = Object.values(states).filter(s => ["overdue", "unverified", "no_schedule"].includes(s.status)).length;
  const previewValid = preview?.schedule === form.schedule.trim().split(/\s+/).join(" ") && preview.requestedTimezone === form.timezone && !!preview?.next_runs?.length;

  return <div className="space-y-5">
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex-1 min-w-48"><h1 className="text-lg font-semibold">周期调度</h1><p className="text-xs text-text-muted mt-1">安排研究任务，确认下一次运行与实际执行状态。</p></div>
      <button onClick={() => setShowHelp(!showHelp)} className="btn text-xs"><HelpCircle className="w-3.5 h-3.5" />帮助</button>
      <button onClick={() => fetchJobs()} disabled={loading || !!busy} className="btn text-xs"><RefreshCw className={cn("w-3.5 h-3.5", loading && "animate-spin")} />刷新</button>
      <button disabled={!!busy} onClick={() => showForm ? setShowForm(false) : openNew()} className="btn btn-primary text-xs">{showForm ? "收起表单" : "+ 新建调度"}</button>
    </div>

    <div className="card p-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
      <span><strong className="text-lg mr-1.5">{jobs.filter(j => j.enabled).length}</strong>已启用</span>
      <span className="text-text-muted">{jobs.filter(j => !j.enabled).length} 个已暂停</span>
      <span className={needsAttention ? "text-warning" : "text-text-muted"}>{needsAttention} 个待检查 / 验证</span>
      <span className="ml-auto text-xs text-text-muted flex items-center gap-1.5"><Clock className="w-3.5 h-3.5" />{timezone}</span>
    </div>

    {dispatcher && <div className={cn("card p-4 flex flex-wrap items-center gap-3", dispatcher.online ? "border-success/30" : "border-danger/50")}>
      <ShieldCheck className={cn("w-5 h-5", dispatcher.online ? "text-success" : "text-danger")} />
      <div className="flex-1"><p className="text-sm font-medium">{dispatcher.online ? "周期执行器在线" : "周期执行器离线"}</p><p className="text-xs text-text-muted mt-1">{dispatcher.detail}。新 job 使用托管计划，关闭页面或重启 Web 不影响执行。</p></div>
      {!dispatcher.online && <button className="btn text-xs" disabled={!!busy} onClick={startDispatcher}>{busy === "host" ? "正在验证心跳…" : "启动 / 恢复执行器"}</button>}
    </div>}
    {hasLaunchd && <div className="card border-accent/30 p-4 flex gap-3">
      <ShieldCheck className="w-5 h-5 text-accent shrink-0" /><div className="text-sm space-y-1">
        <p className="font-medium">情报矩阵由系统定时运行，并定期检查漏跑</p>
        <p className="text-xs text-text-muted">关闭页面不影响调度。补偿任务检查当天轮次；运行结果可在<a className="text-accent mx-1" href="/rounds">轮次看板</a>查看。系统任务在下方单独标识。</p>
      </div>
    </div>}
    {showHelp && <div className="card p-4 text-sm space-y-2">
      <p>选择研究任务和执行时间即可创建计划；默认每天 08:00。时间按服务器所在机器的时区计算。</p>
      <p className="text-text-muted">新计划由系统托管执行器统一处理。休眠、服务中断后默认合并补跑最新一次；同一 job 不重叠执行。电脑仍需开机并登录，关机期间不能运行。</p>
      <p className="text-text-muted">失败采用有限重试，默认最多 2 次（1 分钟、5 分钟后）；预算耗尽、job 停用或不存在会明确记录原因。暂停只影响后续执行，不取消正在运行的任务。</p>
      <p className="text-text-muted">托管计划以实际报告生成结果判定成功，执行和重试记录可在卡片中查看。旧 cron 和情报矩阵专用系统任务单独标识。</p>
    </div>}
    {warnings.map(w => <div key={w} role="alert" className="card border-warning/50 p-3 text-sm text-warning">{w}</div>)}
    {msg && <div role="alert" className="card border-danger/50 p-3 text-sm text-danger">{msg}</div>}

    {showForm && <div className="card p-5 space-y-5">
      <div><h2 className="font-medium">{editing ? `编辑计划 · ${editing}` : "新建研究计划"}</h2><p className="text-xs text-text-muted mt-1">{editing ? "修改后保留当前启用或暂停状态。" : "选择任务 → 设置频率 → 确认下次运行。保存时会验证执行器在线。"}</p></div>
      {!editing && <div className="space-y-2">
        <label htmlFor="job-query" className="text-xs text-text-muted">研究任务</label>
        <input id="job-query" className="input" placeholder="搜索 job 名称或描述…（如 radar、daily、practical）" value={jobQuery} onChange={e => setJobQuery(e.target.value)} />
        {catalogLoading ? <p className="text-xs text-text-muted">正在读取任务…</p> : catalogError ? <div className="text-xs text-danger">{catalogError} <button className="btn ml-2" onClick={fetchCatalog}>重试任务列表</button></div> : <div className="max-h-44 overflow-y-auto space-y-1 border border-border rounded-md p-1.5">
          {!filteredJobs.length && <p className="p-2 text-xs text-text-muted">{schedulable.length ? "没有匹配的任务" : "还没有可调度任务，请先在任务页创建。"}</p>}
          {filteredJobs.map(job => <button key={job.name} type="button" disabled={!job.enabled} aria-pressed={selectedJob === job.name} onClick={() => applyJob(job)}
            className={cn("w-full text-left p-2 rounded hover:bg-bg-hover flex items-center gap-2", selectedJob === job.name && "bg-accent/10 ring-1 ring-inset ring-accent")}>
            <div className="min-w-0 flex-1"><div className="text-xs font-medium">{job.label}<span className="ml-2 text-text-muted font-mono text-[10px]">{job.name}</span></div><p className="text-xs text-text-muted truncate">{job.description}</p></div>
            {job.schedule_id && <span className="text-[10px] text-accent shrink-0">已有计划 · 编辑</span>}
            {!job.enabled && <span className="text-[10px] text-text-muted shrink-0">已停用</span>}
          </button>)}
        </div>}
      </div>}
      <div className="space-y-2"><h3 className="text-xs text-text-muted">重复频率</h3><CronBuilder key={editing || "new"} value={form.schedule} onChange={schedule => setForm(prev => ({ ...prev, schedule }))} /></div>
      {!legacyEdit && <div className="grid sm:grid-cols-3 gap-3 text-xs text-text-muted">
        <label>执行时区<select aria-label="执行时区" className="input mt-1" value={form.timezone} onChange={e => setForm({ ...form, timezone: e.target.value })}>
          {['local', 'Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/London'].map(tz => <option key={tz} value={tz}>{tz === 'local' ? '跟随本机时区' : tz}</option>)}
          {!['local', 'Asia/Shanghai', 'UTC', 'America/New_York', 'Europe/London'].includes(form.timezone) && <option value={form.timezone}>{form.timezone}</option>}
        </select></label>
        <label>错过计划时<select aria-label="补跑策略" className="input mt-1" value={form.missed_policy} onChange={e => setForm({ ...form, missed_policy: e.target.value })}><option value="latest">合并补跑最新一次（推荐）</option><option value="skip">跳过，等待下次计划</option></select></label>
        <label>失败重试<select aria-label="失败重试" className="input mt-1" value={form.max_retries} onChange={e => setForm({ ...form, max_retries: Number(e.target.value) })}>{[0,1,2,3,4,5].map(n => <option key={n} value={n}>{n === 0 ? '不重试' : `最多 ${n} 次`}</option>)}</select></label>
      </div>}
      <div className="bg-bg-hover rounded-lg p-3 space-y-2" aria-live="polite">
        <div className="text-xs font-medium">接下来 3 次计划时间 <span className="text-text-muted font-normal">· {preview?.timezone || timezone}</span></div>
        {previewLoading ? <p className="text-xs text-text-muted">正在计算…</p> : previewError ? <p role="alert" className="text-xs text-danger">{previewError}</p> : <div className="flex flex-wrap gap-2">{preview?.next_runs?.map(run => <span key={run} className="text-xs font-mono border border-border rounded px-2 py-1">{displayTime(run)}</span>)}</div>}
        {editing && !jobs.find(j => j.id === editing)?.enabled && <p className="text-xs text-warning">当前已暂停，以上仅为预览；启用后才会执行。</p>}
      </div>
      <details className="text-xs text-text-muted"><summary className="cursor-pointer">高级设置 · 调度标识</summary><div className="mt-3 space-y-3">
        <label className="block">调度 ID（可选，留空自动生成）<input className="input mt-1" placeholder="如: daily_report, weekly_summary" value={form.id} disabled={!!editing} onChange={e => setForm({ ...form, id: e.target.value })} /></label>
        {legacyEdit && <label className="block">执行命令<textarea className="input mt-1 font-mono" rows={3} placeholder="如: cd /path && python run.py daily_ai_agents" value={form.command} onChange={e => setForm({ ...form, command: e.target.value })} /></label>}
        <p>执行器直接调用选中的 job，无需填写 Shell 命令。API 密钥由项目 .env 加载。</p>
      </div></details>
      <div className="flex justify-end gap-2"><button className="btn" disabled={!!busy} onClick={() => setShowForm(false)}>取消</button><button className="btn btn-primary" onClick={save} disabled={!!busy || !(legacyEdit ? form.command.trim() : form.job_name) || !previewValid || previewLoading}>{busy === "save" ? "保存中…" : editing ? "保存修改" : "创建调度"}</button></div>
    </div>}

    {loading ? <p className="text-sm text-text-muted">加载中…</p> : error ? <ErrorState error={error} onRetry={() => fetchJobs()} /> : jobs.length === 0 ? <div className="card p-10 text-center space-y-2">
      <Clock className="w-8 h-8 mx-auto text-text-muted" /><p className="text-sm font-medium">暂无周期调度任务</p><p className="text-xs text-text-muted">创建计划后，可在这里查看下一次运行和执行状态。</p><button className="btn mt-2" onClick={openNew}>创建第一个计划</button>
    </div> : <div className="space-y-3">{jobs.map(job => {
      const state = states[job.id];
      const editable = job.editable !== false && job.backend !== "launchd";
      return <div key={job.id} className={cn("card p-4 space-y-3", state?.status === "overdue" && "border-danger/50")}>
        <div className="flex flex-wrap items-start gap-3"><div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2"><h2 className="text-sm font-medium break-all">{job.title || job.id}</h2><span className="text-[10px] border border-border rounded px-1.5 py-0.5 text-text-muted">{job.backend === "managed" ? "托管计划 · 自动补跑" : job.backend === "launchd" ? "情报矩阵 · 系统任务" : "旧 cron"}</span>
            <span className={cn("text-xs", !job.enabled ? "text-text-muted" : state?.status === "ok" ? "text-success" : "text-warning")}>{!job.enabled ? "已暂停" : state ? (job.backend === "managed" && state.status === "ok" ? "正常" : STATUS[state.status]) : "待验证"}</span></div>
          <p className="mt-1.5 text-sm text-text-muted">{job.schedule_label || scheduleSummary(expression(job))}{job.backend === "managed" && <span className="ml-2 text-xs">· {job.timezone === "local" ? timezone : job.timezone}</span>}</p>
        </div>
        {editable && <div className="flex gap-1">
          <button className="btn text-xs" disabled={!!busy} onClick={() => openEdit(job)} aria-label={`编辑 ${job.id}`}><Pencil className="w-3 h-3" />编辑</button>
          <button className="btn text-xs" disabled={!!busy} onClick={() => mutate(job, false)} aria-label={`${job.enabled ? "暂停" : "启用"} ${job.id}`}>{job.enabled ? <Pause className="w-3 h-3" /> : <Play className="w-3 h-3" />}{job.enabled ? "暂停" : "启用"}</button>
          <button className="btn text-danger" disabled={!!busy} onClick={() => mutate(job, true)} aria-label={`删除 ${job.id}`}><Trash2 className="w-3 h-3" /></button>
        </div>}</div>
        <div className="grid sm:grid-cols-2 gap-2 text-xs"><p><span className="text-text-muted mr-2">下次计划</span>{!job.enabled ? "暂停期间不执行" : job.next_runs?.[0] ? displayTime(job.next_runs[0]) : job.backend === "launchd" ? "由系统安排检查" : "暂无预览"}</p><p><span className="text-text-muted mr-2">{job.backend === "managed" ? "最近执行" : "最近运行证据"}</span>{displayTime(state?.last_ran_at)}</p></div>
        {state && <div role={state.status === "overdue" ? "alert" : undefined} className={cn("text-xs p-2.5 rounded bg-bg-hover", state.status === "overdue" ? "text-danger" : "text-text-muted")}>
          {state.status === "overdue" && <p className="flex gap-1.5 items-center font-medium mb-1"><AlertTriangle className="w-3.5 h-3.5" />{job.backend === "managed" ? "执行异常" : "漏跑"}：{job.id}</p>}
          {state.status === "paused" && <p className="mb-1">已暂停：{job.id}</p>}{state.detail}
        </div>}
        {job.backend === "managed" ? <div className="space-y-3 border-t border-border pt-2">
          <div className="flex flex-wrap justify-between gap-2 text-xs text-text-muted"><span>{job.missed_policy === "skip" ? "错过即跳过" : "错过时合并补跑"} · 失败最多重试 {job.max_retries} 次</span><button className="text-accent" onClick={() => setHistoryId(historyId === job.id ? "" : job.id)}>{historyId === job.id ? "收起执行记录" : "查看执行记录"}</button></div>
          {historyId === job.id && <ScheduleRuns id={job.id} />}
        </div> : <details className="text-xs text-text-muted"><summary className="cursor-pointer">{editable ? "查看执行命令" : "系统管理说明"}</summary><div className="mt-2 space-y-2">
          {!editable && <p>此任务由系统安装脚本管理，本页只读。调整后需重新安装；不要在轮次运行中重新加载任务。</p>}
          <code className="block whitespace-pre-wrap break-all">{job.command}</code>
        </div></details>}
      </div>;
    })}</div>}
  </div>;
}
