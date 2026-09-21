import { useState, useCallback } from "react";
import { cn } from "@/lib/utils";

interface CronBuilderProps {
  value: string;
  onChange: (cron: string) => void;
}

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

const MINUTES = [
  { label: "每整分", value: "*" },
  { label: "每 5 分", value: "*/5" },
  { label: "每 10 分", value: "*/10" },
  { label: "每 15 分", value: "*/15" },
  { label: "每 30 分", value: "*/30" },
  ...Array.from({ length: 60 }, (_, i) => ({ label: `${i} 分`, value: String(i) })),
];

const HOURS = [
  { label: "每小时", value: "*" },
  { label: "每 2 小时", value: "*/2" },
  { label: "每 4 小时", value: "*/4" },
  { label: "每 6 小时", value: "*/6" },
  { label: "每 12 小时", value: "*/12" },
  ...Array.from({ length: 24 }, (_, i) => ({ label: `${i} 时`, value: String(i) })),
];

const DAYS_OF_MONTH = [
  { label: "每天", value: "*" },
  ...Array.from({ length: 31 }, (_, i) => ({ label: `${i + 1} 号`, value: String(i + 1) })),
];

const MONTHS = [
  { label: "每月", value: "*" },
  { label: "每 3 月", value: "*/3" },
  { label: "每 6 月", value: "*/6" },
  ...Array.from({ length: 12 }, (_, i) => ({ label: `${i + 1} 月`, value: String(i + 1) })),
];

const DAYS_OF_WEEK = [
  { label: "每天", value: "*" },
  { label: "工作日", value: "1-5" },
  { label: "周末", value: "0,6" },
  { label: "周一", value: "1" },
  { label: "周二", value: "2" },
  { label: "周三", value: "3" },
  { label: "周四", value: "4" },
  { label: "周五", value: "5" },
  { label: "周六", value: "6" },
  { label: "周日", value: "0" },
];

function parseCron(cron: string): { m: string; h: string; dom: string; mon: string; dow: string } {
  const parts = cron.trim().split(/\s+/);
  return {
    m: parts[0] || "*",
    h: parts[1] || "*",
    dom: parts[2] || "*",
    mon: parts[3] || "*",
    dow: parts[4] || "*",
  };
}

export function CronBuilder({ value, onChange }: CronBuilderProps) {
  const fields = parseCron(value);
  const [activePreset, setActivePreset] = useState<string | null>(null);

  const updateField = useCallback(
    (field: "m" | "h" | "dom" | "mon" | "dow", val: string) => {
      const next = { ...fields, [field]: val };
      onChange(`${next.m} ${next.h} ${next.dom} ${next.mon} ${next.dow}`);
      setActivePreset(null);
    },
    [fields, onChange]
  );

  const applyPreset = useCallback(
    (cron: string) => {
      onChange(cron);
      setActivePreset(cron);
    },
    [onChange]
  );

  const selectClass = (selected: string, current: string) =>
    selected === current ? "bg-accent text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover";

  return (
    <div className="space-y-3">
      {/* Presets */}
      <div>
        <label className="block text-xs text-text-muted mb-1.5">常用预设</label>
        <div className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <button
              key={p.cron}
              type="button"
              onClick={() => applyPreset(p.cron)}
              className={cn(
                "px-2.5 py-1 text-xs rounded-md border transition",
                selectClass(activePreset || "", p.cron)
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Custom fields */}
      <div>
        <label className="block text-xs text-text-muted mb-1.5">自定义</label>
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
          <div>
            <div className="text-xs text-text-muted mb-0.5">分</div>
            <select
              className="input text-xs"
              value={fields.m}
              onChange={(e) => updateField("m", e.target.value)}
            >
              {MINUTES.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-xs text-text-muted mb-0.5">时</div>
            <select
              className="input text-xs"
              value={fields.h}
              onChange={(e) => updateField("h", e.target.value)}
            >
              {HOURS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-xs text-text-muted mb-0.5">日</div>
            <select
              className="input text-xs"
              value={fields.dom}
              onChange={(e) => updateField("dom", e.target.value)}
            >
              {DAYS_OF_MONTH.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-xs text-text-muted mb-0.5">月</div>
            <select
              className="input text-xs"
              value={fields.mon}
              onChange={(e) => updateField("mon", e.target.value)}
            >
              {MONTHS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div>
            <div className="text-xs text-text-muted mb-0.5">周</div>
            <select
              className="input text-xs"
              value={fields.dow}
              onChange={(e) => updateField("dow", e.target.value)}
            >
              {DAYS_OF_WEEK.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* Readonly cron expression display */}
      <div className="text-xs text-text-muted bg-bg-hover px-3 py-1.5 rounded-md font-mono">
        {value}
      </div>
    </div>
  );
}
