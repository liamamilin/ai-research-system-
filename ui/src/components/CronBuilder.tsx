import { useState } from "react";
import { cn } from "@/lib/utils";

type Frequency = "daily" | "weekdays" | "weekly" | "monthly" | "interval" | "custom";
const DAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
const FREQUENCIES: { value: Frequency; label: string }[] = [
  { value: "daily", label: "每天" }, { value: "weekdays", label: "工作日" },
  { value: "weekly", label: "每周" }, { value: "monthly", label: "每月" },
  { value: "interval", label: "固定间隔" }, { value: "custom", label: "自定义 Cron" },
];
const INTERVALS = [
  { value: "0 * * * *", label: "每小时整点" },
  { value: "0 */6 * * *", label: "每 6 小时（从 00:00 起）" },
  { value: "0 */12 * * *", label: "每 12 小时（从 00:00 起）" },
  { value: "*/30 * * * *", label: "每 30 分钟" },
  { value: "*/15 * * * *", label: "每 15 分钟" },
];

function decode(value: string): { mode: Frequency; time: string; day: string } {
  const [m, h, dom, mon, dow] = value.trim().split(/\s+/);
  const time = /^\d+$/.test(m) && /^\d+$/.test(h) ? `${h.padStart(2, "0")}:${m.padStart(2, "0")}` : "08:00";
  if (INTERVALS.some(i => i.value === value)) return { mode: "interval", time, day: "1" };
  if (/^\d+$/.test(m) && /^\d+$/.test(h) && mon === "*") {
    if (dom === "*" && dow === "*") return { mode: "daily", time, day: "1" };
    if (dom === "*" && dow === "1-5") return { mode: "weekdays", time, day: "1" };
    if (dom === "*" && /^[0-7]$/.test(dow)) return { mode: "weekly", time, day: String(Number(dow) % 7) };
    if (/^\d+$/.test(dom) && dow === "*") return { mode: "monthly", time, day: dom };
  }
  return { mode: "custom", time, day: "1" };
}

export function scheduleSummary(value: string): string {
  const { mode, time, day } = decode(value);
  if (mode === "daily") return `每天 ${time}`;
  if (mode === "weekdays") return `工作日 ${time}`;
  if (mode === "weekly") return `每${DAYS[Number(day)]} ${time}`;
  if (mode === "monthly") return `每月 ${day} 日 ${time}`;
  return INTERVALS.find(i => i.value === value)?.label || value;
}

export function CronBuilder({ value, onChange }: { value: string; onChange: (cron: string) => void }) {
  const decoded = decode(value);
  const [advanced, setAdvanced] = useState(false);
  const mode = advanced ? "custom" : decoded.mode;
  const change = (next: Frequency, time = decoded.time, day = decoded.day) => {
    setAdvanced(next === "custom");
    if (next === "custom") return;
    if (next === "interval") { onChange(INTERVALS[0].value); return; }
    const [h, m] = time.split(":").map(Number);
    onChange(`${m} ${h} ${next === "monthly" ? day : "*"} * ${next === "weekly" ? day : next === "weekdays" ? "1-5" : "*"}`);
  };
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="重复频率">
      {FREQUENCIES.map(f => <button key={f.value} type="button" aria-pressed={mode === f.value}
        className={cn("btn text-xs", mode === f.value && "bg-accent/15 border-accent text-accent")}
        onClick={() => change(f.value, decoded.time, "1")}>{f.label}</button>)}
    </div>
    {mode === "custom" ? <div>
      <label htmlFor="cron-expression" className="text-xs text-text-muted">Cron 表达式（分 时 日 月 周）</label>
      <input id="cron-expression" className="input mt-1 font-mono" value={value} onChange={e => onChange(e.target.value)} />
      <p className="text-xs text-text-muted mt-1">日期和星期同时指定时，任一匹配即运行。</p>
    </div> : mode === "interval" ? <select aria-label="执行间隔" className="input" value={value} onChange={e => onChange(e.target.value)}>
      {INTERVALS.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
    </select> : <div className="flex flex-wrap items-center gap-3">
      {mode === "weekly" && <select aria-label="执行星期" className="input w-auto" value={decoded.day} onChange={e => change(mode, decoded.time, e.target.value)}>
        {[1, 2, 3, 4, 5, 6, 0].map(d => <option key={d} value={d}>{DAYS[d]}</option>)}
      </select>}
      {mode === "monthly" && <select aria-label="执行日期" className="input w-auto" value={decoded.day} onChange={e => change(mode, decoded.time, e.target.value)}>
        {Array.from({ length: 31 }, (_, i) => <option key={i + 1} value={i + 1}>每月 {i + 1} 日</option>)}
      </select>}
      <label className="flex items-center gap-2 text-xs text-text-muted">执行时间
        <input type="time" aria-label="执行时间" className="input w-auto" value={decoded.time} onChange={e => { if (e.target.value) change(mode, e.target.value); }} />
      </label>
    </div>}
    {mode === "monthly" && Number(decoded.day) > 28 && <p className="text-xs text-warning">没有 {decoded.day} 日的月份会跳过执行。</p>}
    {mode !== "custom" && <p className="text-xs text-text-muted">{scheduleSummary(value)} <code className="ml-2 opacity-60">{value}</code></p>}
  </div>;
}
