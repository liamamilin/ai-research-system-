import { useEffect, useState } from "react";
import { Check, RotateCcw, X, XCircle } from "lucide-react";

import {
  getRoundDiff,
  getTrackedItems,
  updateTrackedItem,
  type CarryOver,
  type DiffRow,
  type RoundDiff,
  type TrackedItem,
} from "@/api";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";

type Tab = "actions" | "watch" | "diff";

const PRIORITY_STYLE: Record<string, string> = {
  P0: "border-danger/60 text-danger",
  P1: "border-warning/60 text-warning",
  P2: "border-border text-text-muted",
};

function PriorityBadge({ value }: { value: string }) {
  if (!value) return null;
  return (
    <span className={cn("badge border text-[10px] px-1 py-0", PRIORITY_STYLE[value] || PRIORITY_STYLE.P2)}>
      {value}
    </span>
  );
}

interface ItemRowProps {
  item: TrackedItem;
  current: boolean;
  canEdit: boolean;
  onToggle: (item: TrackedItem) => void;
  onDrop: (item: TrackedItem) => void;
  onNote: (item: TrackedItem) => void;
}

function ItemRow({ item, current, canEdit, onToggle, onDrop, onNote }: ItemRowProps) {
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-border/50 last:border-0">
      <PriorityBadge value={item.priority} />
      <div className="flex-1 min-w-0">
        <div className={cn("text-xs", item.status !== "open" && "line-through text-text-muted")}>
          {item.text}
        </div>
        <div className="text-[10px] text-text-muted mt-0.5">
          {current ? "本轮" : `上次出现 ${item.last_seen}`}
          {item.times_seen > 1 && ` · 累计 ${item.times_seen} 次`}
          {item.first_seen !== item.last_seen && ` · 首见 ${item.first_seen}`}
          {item.status === "done" && " · 已完成"}
          {item.status === "dropped" && " · 已放弃"}
          {item.note && ` · ${item.note}`}
        </div>
      </div>
      {canEdit && (
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => onNote(item)}
            className="text-[10px] text-text-muted hover:text-text px-1"
            title="编辑备注"
          >
            备注
          </button>
          <button
            onClick={() => onToggle(item)}
            className={cn(
              "p-1 rounded transition",
              item.status === "done"
                ? "text-success hover:bg-bg-hover"
                : "text-text-muted hover:text-success hover:bg-bg-hover",
            )}
            title={item.status === "done" ? "重新打开" : "标记完成"}
          >
            {item.status === "done" ? <RotateCcw className="w-3.5 h-3.5" /> : <Check className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => onDrop(item)}
            className="p-1 rounded text-text-muted hover:text-danger hover:bg-bg-hover transition"
            title="放弃该项"
          >
            <XCircle className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}

function DiffList({ title, items, keyName }: { title: string; items: DiffRow[]; keyName: string }) {
  if (items.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-xs font-medium">{title}（{items.length}）</div>
      <div className="pl-2 space-y-0.5">
        {items.map((item, index) => (
          <div key={`${index}-${item[keyName]}`} className="text-xs text-text-muted">
            · {item[keyName]}
          </div>
        ))}
      </div>
    </div>
  );
}

export function RoundDetail({ date, onClose }: { date: string; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("actions");
  const [carry, setCarry] = useState<CarryOver | null>(null);
  const [items, setItems] = useState<TrackedItem[]>([]);
  const [diff, setDiff] = useState<RoundDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  const patchItem = async (
    item: TrackedItem,
    payload: Partial<Pick<TrackedItem, "status" | "note">>,
  ) => {
    const previous = { status: item.status, note: item.note };
    setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, ...payload } : i)));
    setError("");
    try {
      await updateTrackedItem(item.id, payload);
    } catch (err: unknown) {
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, ...previous } : i)));
      setError(err instanceof Error ? err.message : "更新失败");
    }
  };

  const handleToggle = (item: TrackedItem) =>
    patchItem(item, { status: item.status === "done" ? "open" : "done" });

  const handleDrop = (item: TrackedItem) => {
    if (!window.confirm("放弃该项？可在列表中重新打开。")) return;
    void patchItem(item, { status: "dropped" });
  };

  const handleNote = (item: TrackedItem) => {
    const note = window.prompt("备注：", item.note || "");
    if (note === null) return;
    void patchItem(item, { note });
  };

  const rowProps = { canEdit, onToggle: handleToggle, onDrop: handleDrop, onNote: handleNote };

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    Promise.all([
      getTrackedItems({ limit: 1000 }),
      getRoundDiff(date).catch(() => null),
    ])
      .then(([tracking, diffData]) => {
        if (!alive) return;
        setItems(tracking.items);
        setCarry(tracking.carry_over);
        setDiff(diffData);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "加载失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [date]);

  const actions = items.filter((i) => i.kind === "action" || i.kind === "test");
  const watch = items.filter((i) => i.kind === "watch");
  const currentActions = actions.filter((i) => i.last_seen === date);
  const staleActions = actions.filter((i) => i.status === "open" && i.last_seen !== date);
  const currentWatch = watch.filter((i) => i.last_seen === date);

  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-full max-w-2xl h-full bg-bg-card border-l border-border flex flex-col">
        <div className="px-4 py-3 border-b border-border flex items-center gap-3">
          <span className="font-mono text-sm">{date}</span>
          <span className="text-xs text-text-muted">轮次详情</span>
          <button onClick={onClose} className="ml-auto text-text-muted hover:text-text" title="关闭">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="px-4 pt-2 flex gap-1 border-b border-border">
          {([
            ["actions", `行动（${currentActions.length}）`],
            ["watch", `观察（${currentWatch.length}）`],
            ["diff", "对比上一轮"],
          ] as [Tab, string][]).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={cn(
                "px-3 py-1.5 text-xs rounded-t-md border-b-2 transition",
                tab === key
                  ? "border-accent text-text"
                  : "border-transparent text-text-muted hover:text-text",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
          {loading && <div className="text-sm text-text-muted">加载中...</div>}
          {error && <div className="text-sm text-danger">{error}</div>}

          {!loading && tab === "actions" && (
            <>
              {currentActions.length === 0 && staleActions.length === 0 && (
                <div className="text-sm text-text-muted">暂无跟踪的行动项（运行轮次后自动同步）。</div>
              )}
              {currentActions.length > 0 && (
                <div>
                  <div className="text-xs font-medium mb-1">本轮行动</div>
                  {currentActions.map((item) => <ItemRow key={item.id} item={item} current {...rowProps} />)}
                </div>
              )}
              {staleActions.length > 0 && (
                <div>
                  <div className="text-xs font-medium mb-1">往轮未完成（延续）</div>
                  {staleActions.map((item) => <ItemRow key={item.id} item={item} current={false} {...rowProps} />)}
                </div>
              )}
            </>
          )}

          {!loading && tab === "watch" && (
            <>
              {watch.length === 0 && (
                <div className="text-sm text-text-muted">暂无观察项。</div>
              )}
              {watch.map((item) => (
                <ItemRow key={item.id} item={item} current={item.last_seen === date} {...rowProps} />
              ))}
            </>
          )}

          {!loading && tab === "diff" && (
            <>
              {!diff || !diff.from ? (
                <div className="text-sm text-text-muted">
                  {diff ? "没有更早的轮次可对比。" : "diff 不可用。"}
                </div>
              ) : (
                <div className="space-y-4">
                  <div className="text-xs text-text-muted">
                    {diff.from} → {diff.to}
                  </div>
                  <DiffList title="新增行动" items={diff.actions.added} keyName="action" />
                  <DiffList title="移除行动" items={diff.actions.removed} keyName="action" />
                  <DiffList title="新增测试" items={diff.tests.added} keyName="test" />
                  <DiffList title="新增观察" items={diff.watchlist.added} keyName="topic" />
                  <DiffList title="移除观察" items={diff.watchlist.removed} keyName="topic" />
                  {diff.sources.added.length > 0 && (
                    <div className="space-y-1">
                      <div className="text-xs font-medium">
                        新增来源（{diff.sources.added.length}）
                      </div>
                      {diff.sources.new_domains.length > 0 && (
                        <div className="text-[10px] text-text-muted">
                          新域名：{diff.sources.new_domains.join("、")}
                        </div>
                      )}
                      <div className="pl-2 space-y-0.5">
                        {diff.sources.added.slice(0, 20).map((url) => (
                          <div key={url} className="text-xs text-text-muted truncate">
                            ·{" "}
                            <a href={url} target="_blank" rel="noreferrer"
                               className="hover:text-text hover:underline">
                              {url}
                            </a>
                          </div>
                        ))}
                        {diff.sources.added.length > 20 && (
                          <div className="text-[10px] text-text-muted">
                            仅显示前 20 条
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                  {Object.values(diff.counts).every((v) => v === 0) && (
                    <div className="text-sm text-text-muted">两轮内容无差异。</div>
                  )}
                </div>
              )}
            </>
          )}

          {!loading && carry && (
            <div className="text-[10px] text-text-muted border-t border-border pt-2">
              跟踪库：本轮新增 {carry.new.length} · 延续 {carry.continuing.length} · 往轮未完成 {carry.open_stale.length}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
