import { useMemo, useState } from "react";
import type { JobTemplate } from "@/api";
import { cn } from "@/lib/utils";
import { Modal } from "@/components/Modal";

const CATEGORY_LABELS: Record<string, string> = {
  monitoring: "监控速报",
  research: "深度研究",
  analysis: "分析洞察",
  practice: "落地实践",
  actionable: "行动决策",
  "": "未分类",
};

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] || category || "未分类";
}

interface Props {
  templates: JobTemplate[];
  selectedKey?: string;
  onSelect?: (t: JobTemplate) => void;
  onEdit?: (t: JobTemplate) => void;
  onDelete?: (t: JobTemplate) => void;
  emptyHint?: string;
  compact?: boolean;
}

export function TemplateGrid({
  templates,
  selectedKey,
  onSelect,
  onEdit,
  onDelete,
  emptyHint = "暂无模板",
  compact = false,
}: Props) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [preview, setPreview] = useState<JobTemplate | null>(null);

  const categoryOptions = useMemo(() => {
    const set = new Set<string>();
    for (const t of templates) if (t?.category) set.add(t.category);
    return [...set].sort();
  }, [templates]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (templates || []).filter((t) => {
      if (!t) return false;
      if (category && t.category !== category) return false;
      if (!q) return true;
      return [t.label, t.key, t.description, t.category]
        .filter(Boolean)
        .some((v) => String(v).toLowerCase().includes(q));
    });
  }, [templates, query, category]);

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="input max-w-[200px] text-xs"
          placeholder="搜索模板（名称/描述）..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            onClick={() => setCategory("")}
            title="筛选：全部"
            className={cn(
              "badge border cursor-pointer",
              !category ? "bg-accent text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover"
            )}
          >
            全部
          </button>
          {categoryOptions.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              title={`筛选：${categoryLabel(c)}`}
              className={cn(
                "badge border cursor-pointer",
                category === c ? "bg-accent text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover"
              )}
            >
              {categoryLabel(c)}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="text-xs text-text-muted py-3">{emptyHint}</div>
      ) : (
        <div className={cn("grid gap-2", compact ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2")}>
          {filtered.map((t) => (
            <div
              key={t.key}
              className={cn(
                "rounded-md border p-2.5 text-left transition",
                selectedKey === t.key
                  ? "border-accent bg-accent/5"
                  : "border-border bg-bg-card hover:bg-bg-hover"
              )}
            >
              <div className="flex items-start gap-2">
                <button
                  type="button"
                  disabled={!onSelect}
                  onClick={() => onSelect?.(t)}
                  className="flex-1 text-left disabled:cursor-default"
                >
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-sm font-medium">{t.label || t.key}</span>
                    {t.builtin && (
                      <span className="badge border border-border text-[10px] text-text-muted">内置</span>
                    )}
                    {t.category && (
                      <span className="badge border border-border text-[10px] text-text-muted">
                        {categoryLabel(t.category)}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-text-muted mt-0.5 line-clamp-2">{t.description || ""}</p>
                  {(t.variables || []).length > 0 && (
                    <p className="text-[10px] text-text-muted/70 mt-1 font-mono">
                      变量：{(t.variables || []).map((v) => `{${v}}`).join(" ")}
                    </p>
                  )}
                </button>
                <div className="flex flex-col gap-1 shrink-0">
                  <button
                    type="button"
                    onClick={() => setPreview(t)}
                    className="text-[11px] text-text-muted hover:text-accent"
                  >
                    预览
                  </button>
                  {onEdit && (
                    <button
                      type="button"
                      onClick={() => onEdit(t)}
                      className="text-[11px] text-text-muted hover:text-accent"
                    >
                      编辑
                    </button>
                  )}
                  {onDelete && !t.builtin && (
                    <button
                      type="button"
                      onClick={() => onDelete(t)}
                      className="text-[11px] text-text-muted hover:text-danger"
                    >
                      删除
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {preview && (
        <Modal open={!!preview} onClose={() => setPreview(null)}
               title={preview.label || preview.key}
               className="w-full max-w-3xl max-h-[85vh]">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2">
              <span className="font-medium text-sm">{preview.label || preview.key}</span>
              <span className="badge border border-border text-[10px] text-text-muted">
                {preview.key}
              </span>
              <button
                type="button"
                onClick={() => setPreview(null)}
                className="ml-auto text-text-muted hover:text-foreground text-sm"
              >
                关闭
              </button>
            </div>
            <div className="px-4 py-3 overflow-auto text-xs space-y-3">
              {preview.description && (
                <p className="text-text-muted">{preview.description}</p>
              )}
              <div className="grid grid-cols-2 gap-2 text-text-muted">
                <div>输出路径模板：<span className="font-mono">{preview.output_template}</span></div>
                <div>语言：{preview.language || "zh"}</div>
                <div>超时：{preview.timeout_seconds ? `${preview.timeout_seconds}s` : "默认"}</div>
                <div>更新：{preview.updated_at}</div>
              </div>
              <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed bg-bg-hover rounded p-3">
                {preview.prompt || preview.content}
              </pre>
            </div>
        </Modal>
      )}
    </div>
  );
}
