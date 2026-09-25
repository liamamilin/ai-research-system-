import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { errorMessage } from '@/lib/toast';

interface Attempt { attempt: number; status: string; error?: string }
interface Run { id: string; due_at_iso: string; started_at_iso?: string; status: string; attempt: number; error?: string; output_path?: string; timezone?: string; attempts: Attempt[] }
const LABELS: Record<string, string> = { success: '成功', failed: '失败', retry: '等待重试', running: '运行中', starting: '启动中', queued: '排队中', interrupted: '进程中断', skipped: '已跳过', blocked: '未执行' };

// The server sends the wall clock in the plan's own timezone. Cutting it to
// "2026-09-25 03:00" hides which zone that is, and around a DST change the same
// wall clock happens twice.
const format = (at: string) => {
  const match = /^(.*?)(Z|[+-]\d{2}:?\d{2})$/.exec(at);
  if (!match) return at.replace('T', ' ').slice(0, 19);
  return `${match[1].replace('T', ' ')}${match[2] === 'Z' ? ' UTC' : ` UTC${match[2]}`}`;
};

export function ScheduleRuns({ id }: { id: string }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError('');
    api<{runs: Run[]}>(`/api/scheduler/${encodeURIComponent(id)}/runs`)
      .then(data => { if (active) setRuns(data.runs); })
      .catch(err => { if (active) setError(errorMessage(err)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [id, revision]);
  return <div className="space-y-2 text-xs">
    <div className="flex items-center justify-between"><span className="text-text-muted">最近 20 次计划执行（含重试记录）</span><button className="btn text-xs" disabled={loading} onClick={() => setRevision(v => v + 1)}>刷新记录</button></div>
    {loading ? <p>正在读取执行记录…</p> : error ? <p role="alert" className="text-danger">{error}</p> : !runs.length ? <p className="text-text-muted">还没有到期执行记录。</p> : runs.map(run => <div key={run.id} className="rounded border border-border p-3 space-y-1">
      <div className="flex flex-wrap gap-3"><span>计划 {format(run.due_at_iso)}</span><strong>{LABELS[run.status] || run.status}</strong><span className="text-text-muted">已尝试 {run.attempt} 次</span>{run.timezone && <span className="text-text-muted">时区 {run.timezone}</span>}</div>
      {run.started_at_iso && <p className="text-text-muted">实际开始 {format(run.started_at_iso)}</p>}
      {run.error && <p className="text-warning break-words">{run.error}</p>}
      {run.output_path && <p className="text-text-muted break-all">报告：{run.output_path}</p>}
      {run.attempts?.length > 1 && <p className="text-text-muted">{run.attempts.map(a => `第 ${a.attempt} 次：${LABELS[a.status] || a.status}${a.error ? `（${a.error}）` : ''}`).join('；')}</p>}
    </div>)}
  </div>;
}
