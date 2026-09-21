import { useEffect, useState } from "react";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";
import { Clock, Trash2, Play, Pause, HelpCircle } from "lucide-react";
import { CronBuilder } from "@/components/CronBuilder";

interface CronJob {
  id: string;
  minute: string;
  hour: string;
  day_of_month: string;
  month: string;
  day_of_week: string;
  command: string;
  enabled: boolean;
}

export function SchedulerPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [newJob, setNewJob] = useState({ id: "", schedule: "", command: "" });
  const [msg, setMsg] = useState("");

  const fetchJobs = async () => {
    setLoading(true);
    try {
      const data = await api<{ jobs: CronJob[]; total: number }>("/api/scheduler");
      setJobs(data.jobs);
    } catch (err: any) {
      setError(err.message || "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchJobs(); }, []);

  const handleAdd = async () => {
    setMsg("");
    try {
      await api("/api/scheduler", {
        method: "POST",
        body: { id: newJob.id, schedule: newJob.schedule, command: newJob.command },
      });
      setNewJob({ id: "", schedule: "", command: "" });
      setShowForm(false);
      fetchJobs();
    } catch (err: any) {
      setMsg(err.message || "创建失败");
    }
  };

  const handleDelete = async (id: string) => {
    if (!window.confirm(`确定删除 schedule '${id}'?`)) return;
    try {
      await api(`/api/scheduler/${encodeURIComponent(id)}`, { method: "DELETE" });
      fetchJobs();
    } catch (err: any) {
      setMsg(err.message || "删除失败");
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      await api(`/api/scheduler/${encodeURIComponent(id)}/toggle`, {
        method: "PUT",
        body: { enabled: !enabled },
      });
      fetchJobs();
    } catch (err: any) {
      setMsg(err.message || "操作失败");
    }
  };

  const PRESETS = [
    { label: "每小时", cron: "0 * * * *" },
    { label: "每 6 小时", cron: "0 */6 * * *" },
    { label: "每天 6:00", cron: "0 6 * * *" },
    { label: "每天 8:00", cron: "0 8 * * *" },
    { label: "每天 9:00", cron: "0 9 * * *" },
    { label: "工作日 6:00", cron: "0 6 * * 1-5" },
    { label: "工作日 8:00", cron: "0 8 * * 1-5" },
    { label: "每星期一 6:00", cron: "0 6 * * 1" },
    { label: "每月 1 号 6:00", cron: "0 6 1 * *" },
    { label: "每 30 分钟", cron: "*/30 * * * *" },
    { label: "每 15 分钟", cron: "*/15 * * * *" },
  ];

  const COMMAND_PRESETS = [
    { label: "运行 daily_ai_agents", cmd: "cd /path/to/Base_CodingCLi && python run.py daily_ai_agents" },
    { label: "全量情报矩阵", cmd: "cd /path/to/Base_CodingCLi && bash scripts/run_practical_intelligence.sh" },
    { label: "运行全部 jobs", cmd: "cd /path/to/Base_CodingCLi && python run.py" },
    { label: "自定义...", cmd: "" },
  ];

  const EXAMPLES = [
    {
      title: "每天 8:00 运行每日 AI 监测",
      desc: "自动搜集 AI agents 动态",
      cron: "0 8 * * *",
      cmd: "cd /path/to/Base_CodingCLi && python run.py daily_ai_agents >> logs/daily.log 2>&1",
    },
    {
      title: "工作日 6:00 全量情报矩阵",
      desc: "P0-P9 十个雷达并行搜集",
      cron: "0 6 * * 1-5",
      cmd: "cd /path/to/Base_CodingCLi && bash scripts/run_practical_intelligence.sh >> logs/pipe.log 2>&1",
    },
    {
      title: "每星期一 9:00 一周趋势分析",
      desc: "每周汇总分析报告",
      cron: "0 9 * * 1",
      cmd: "cd /path/to/Base_CodingCLi && python run.py analysis/ai_trends >> logs/weekly.log 2>&1",
    },
    {
      title: "每月 1 号 6:00 月度总结",
      desc: "全量运行所有 job",
      cron: "0 6 1 * *",
      cmd: "cd /path/to/Base_CodingCLi && python run.py >> logs/monthly.log 2>&1",
    },
  ];

  const scheduleSummary = (j: CronJob) => {
    const cron = `${j.minute} ${j.hour} ${j.day_of_month} ${j.month} ${j.day_of_week}`;
    const preset = PRESETS.find((p) => p.cron === cron);
    if (preset) return preset.label;
    const parts: string[] = [];
    if (j.minute !== "*" && j.hour !== "*" && j.day_of_week === "*" && j.day_of_month === "*") {
      parts.push(`每天 ${j.hour.padStart(2, "0")}:${j.minute.padStart(2, "0")}`);
    } else if (j.minute !== "*" && j.hour === "*") {
      parts.push(`每 ${j.minute} 分`);
    } else if (j.day_of_week !== "*") {
      const time = `${j.hour.padStart(2, "0")}:${j.minute.padStart(2, "0")}`;
      parts.push(`星期${j.day_of_week} ${time}`);
    } else {
      parts.push(cron);
    }
    return parts.join(" ");
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">周期调度</h1>
        <button onClick={() => setShowHelp(!showHelp)} className="btn text-xs" title="使用帮助">
          <HelpCircle className="w-3.5 h-3.5" />
          帮助
        </button>
        <button onClick={() => setShowForm(!showForm)} className="btn btn-primary text-xs">
          {showForm ? "取消" : "+ 新建调度"}
        </button>
      </div>

      {/* Help panel */}
      {showHelp && (
        <div className="card p-4 text-sm space-y-3">
          <div>
            <h3 className="font-medium mb-1">什么是周期调度？</h3>
            <p className="text-text-muted">
              周期调度让你在指定时间自动运行研究任务。系统会修改你的系统 crontab 来执行。
              所有调度在后台运行，不影响 Web UI 的操作。
            </p>
          </div>
          <div>
            <h3 className="font-medium mb-1">配置说明</h3>
            <table className="text-xs w-full">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left pr-4 py-1 font-medium text-text-muted">字段</th>
                  <th className="text-left pr-4 py-1 font-medium text-text-muted">说明</th>
                  <th className="text-left py-1 font-medium text-text-muted">示例</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-border/50">
                  <td className="pr-4 py-1.5 font-mono">调度 ID</td>
                  <td className="pr-4 py-1.5 text-text-muted">唯一标识，用来管理/删除</td>
                  <td className="py-1.5 font-mono text-accent">daily_report</td>
                </tr>
                <tr className="border-b border-border/50">
                  <td className="pr-4 py-1.5 font-mono">执行时间</td>
                  <td className="pr-4 py-1.5 text-text-muted">cron 表达式，或点选预设</td>
                  <td className="py-1.5 font-mono text-accent">0 8 * * *</td>
                </tr>
                <tr>
                  <td className="pr-4 py-1.5 font-mono">命令</td>
                  <td className="pr-4 py-1.5 text-text-muted">要执行的 shell 命令</td>
                  <td className="py-1.5 font-mono text-accent text-xs">cd /path && python run.py daily_ai_agents</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div>
            <h3 className="font-medium mb-1">常用示例</h3>
            <div className="space-y-2">
              {EXAMPLES.map((ex, i) => (
                <div key={i} className="bg-bg-hover rounded-md p-2.5">
                  <div className="font-medium text-xs mb-0.5">{ex.title}</div>
                  <div className="text-xs text-text-muted mb-1">{ex.desc}</div>
                  <div className="flex gap-3 text-xs font-mono">
                    <span className="text-accent">{ex.cron}</span>
                    <span className="text-text-muted/60">|</span>
                    <span className="text-text-muted truncate">{ex.cmd}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div>
            <h3 className="font-medium mb-1">注意事项</h3>
            <ul className="text-xs text-text-muted space-y-0.5 list-disc list-inside">
              <li>命令中使用<strong>绝对路径</strong>，或在前面加 <code className="text-xs bg-bg-hover px-1 rounded">cd /path &&</code></li>
              <li>日志输出到 <code className="text-xs bg-bg-hover px-1 rounded">&gt;&gt; logs/xxx.log 2&gt;&amp;1</code></li>
              <li>API key 等密钥统一放在项目 <code className="text-xs bg-bg-hover px-1 rounded">.env</code>（run.py 会自动加载），无需写进命令</li>
              <li>创建后可以随时<strong>暂停</strong>（保留配置）或<strong>删除</strong></li>
            </ul>
          </div>
        </div>
      )}

      {error && (
        <div className="text-sm text-danger bg-red-900/20 px-3 py-2 rounded-md">{error}</div>
      )}
      {msg && <div className="text-sm text-text-muted bg-bg-card px-3 py-2 rounded-md">{msg}</div>}

      {showForm && (
        <div className="card p-4 space-y-3">
          <div>
            <label className="block text-xs text-text-muted mb-1">调度 ID</label>
            <input className="input" placeholder="如: daily_report, weekly_summary" value={newJob.id}
              onChange={(e) => setNewJob({ ...newJob, id: e.target.value })} />
            <p className="text-xs text-text-muted/60 mt-0.5">唯一标识，用于管理/删除。只能包含字母、数字、下划线</p>
          </div>

          <div>
            <label className="block text-xs text-text-muted mb-1">执行时间</label>
            <CronBuilder
              value={newJob.schedule}
              onChange={(cron) => setNewJob({ ...newJob, schedule: cron })}
            />
          </div>

          <div>
            <label className="block text-xs text-text-muted mb-1">命令</label>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {COMMAND_PRESETS.map((p) => (
                <button key={p.label} type="button" onClick={() => setNewJob({ ...newJob, command: p.cmd })}
                  className="px-2.5 py-1 text-xs rounded-md border border-border bg-bg-card hover:bg-bg-hover transition"
                >{p.label}</button>
              ))}
            </div>
            <input className="input font-mono text-xs" placeholder="如: cd /path && python run.py daily_ai_agents" value={newJob.command}
              onChange={(e) => setNewJob({ ...newJob, command: e.target.value })} />
            <p className="text-xs text-text-muted/60 mt-0.5">
              要执行的 shell 命令。使用完整路径，或在命令前加 <code className="text-xs bg-bg-hover px-1 rounded">cd /path/to/project &&</code>
            </p>
          </div>

          <button onClick={handleAdd} className="btn btn-primary w-full justify-center">创建调度</button>
        </div>
      )}

      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : jobs.length === 0 ? (
        <div className="card p-6 text-center text-text-muted text-sm">
          <Clock className="w-8 h-8 mx-auto mb-2 opacity-50" />
          暂无周期调度任务
        </div>
      ) : (
        <div className="space-y-2">
          {jobs.map((j) => (
            <div key={j.id} className={cn("card px-4 py-3", !j.enabled && "opacity-50")}>
              <div className="flex items-center gap-3">
                <button
                  onClick={() => handleToggle(j.id, j.enabled)}
                  className={cn("btn p-1.5", j.enabled ? "text-success" : "text-text-muted")}
                  title={j.enabled ? "暂停" : "启用"}
                >
                  {j.enabled ? <Play className="w-3 h-3" /> : <Pause className="w-3 h-3" />}
                </button>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium">{j.id}</div>
                  <div className="text-xs text-text-muted mt-0.5 space-x-2">
                    <span className="font-mono">{scheduleSummary(j)}</span>
                    <span className="text-border">|</span>
                    <code className="text-xs bg-bg-hover px-1 py-0.5 rounded">{j.command.slice(0, 60)}{j.command.length > 60 ? "..." : ""}</code>
                  </div>
                </div>
                <button
                  onClick={() => handleDelete(j.id)}
                  className="btn p-1.5 text-danger"
                  title="删除"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
