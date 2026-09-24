import { useCallback, useEffect, useState } from "react";
import {
  createTemplate,
  deleteTemplate,
  getJob,
  listJobs,
  listTemplates,
  updateTemplate,
  type JobTemplate,
  type TemplateInput,
} from "@/api";
import { Modal } from "@/components/Modal";
import { TemplateGrid, categoryLabel } from "./TemplateGrid";

const CATEGORIES = ["monitoring", "research", "analysis", "practice", "actionable"];
const SCHEDULE_TYPES = ["manual", "daily", "weekly", "monthly"];

interface Props {
  open: boolean;
  onClose: () => void;
  onUse?: (t: JobTemplate) => void;
}

interface FormState {
  key: string;
  label: string;
  category: string;
  description: string;
  prompt: string;
  keywords: string;
  output: string;
  language: string;
  timeout: string;
  scheduleType: string;
  scheduleTime: string;
}

const EMPTY: FormState = {
  key: "",
  label: "",
  category: "research",
  description: "",
  prompt: "",
  keywords: "",
  output: "",
  language: "zh",
  timeout: "3600",
  scheduleType: "manual",
  scheduleTime: "08:00",
};

export function TemplateLibrary({ open, onClose, onUse }: Props) {
  const [templates, setTemplates] = useState<JobTemplate[]>([]);
  const [mode, setMode] = useState<"list" | "create" | "edit">("list");
  const [editing, setEditing] = useState<JobTemplate | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [jobs, setJobs] = useState<string[]>([]);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await listTemplates();
      setTemplates(data.templates || []);
    } catch (e: any) {
      setErr(e?.message || "模板加载失败");
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    setMsg("");
    setErr("");
    setMode("list");
    setEditing(null);
    setForm(EMPTY);
    refresh();
    listJobs()
      .then((list) => setJobs(list.map((j) => j.name)))
      .catch(() => setJobs([]));
  }, [open, refresh]);

  const startCreate = () => {
    setMode("create");
    setEditing(null);
    setForm(EMPTY);
    setMsg("");
    setErr("");
  };

  const startEdit = (t: JobTemplate) => {
    setMode("edit");
    setEditing(t);
    setErr("");
    setMsg("");
    setForm({
      key: t.key,
      label: t.label || "",
      category: t.category || "research",
      description: t.description || "",
      prompt: t.prompt || "",
      keywords: (t.keywords || []).join(", "),
      output: t.output_template || "",
      language: t.language || "zh",
      timeout: String(t.timeout_seconds || 3600),
      scheduleType: (t.schedule?.type as string) || "manual",
      scheduleTime: (t.schedule?.time as string) || "08:00",
    });
  };

  const importFromJob = async (jobName: string) => {
    if (!jobName) return;
    setErr("");
    try {
      const job: any = await getJob(jobName);
      setForm((f) => ({
        ...f,
        key: f.key || jobName.split("/").pop() || "",
        label: f.label || jobName,
        category: job.category || f.category,
        description: job.description || f.description,
        prompt: job.prompt || f.prompt,
        keywords: (job.keywords || []).join(", "),
        output: job.output_template || f.output,
        language: job.language || f.language,
        timeout: String(job.timeout_seconds || f.timeout),
        scheduleType: (job.schedule?.type as string) || f.scheduleType,
        scheduleTime: (job.schedule?.time as string) || f.scheduleTime,
      }));
      setMode("create");
      setEditing(null);
    } catch (e: any) {
      setErr(e?.message || "读取 job 失败");
    }
  };

  const save = async () => {
    setErr("");
    setMsg("");
    if (!form.label.trim()) return setErr("请填写模板名称");
    if (!form.prompt.trim()) return setErr("请填写 prompt 内容");
    if (!editing && !/^[a-z0-9][a-z0-9_-]{1,48}$/.test(form.key)) {
      return setErr("key 只能是小写字母、数字、下划线、连字符（2-49 字符）");
    }
    const payload: TemplateInput = {
      label: form.label.trim(),
      category: form.category,
      description: form.description.trim(),
      prompt: form.prompt,
      keywords: form.keywords.split(",").map((k) => k.trim()).filter(Boolean),
      language: form.language,
      output: form.output.trim(),
      timeout_seconds: Number(form.timeout) || 3600,
      schedule:
        form.scheduleType === "manual"
          ? null
          : { type: form.scheduleType, time: form.scheduleTime, timezone: "Asia/Shanghai" },
    };
    if (!editing) payload.key = form.key;

    setBusy(true);
    try {
      if (editing) await updateTemplate(editing.key, payload);
      else await createTemplate(payload);
      setMsg(editing ? "模板已更新" : "模板已创建");
      setMode("list");
      setEditing(null);
      setForm(EMPTY);
      await refresh();
    } catch (e: any) {
      setErr(e?.message || "保存失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (t: JobTemplate) => {
    if (!window.confirm(`删除模板「${t.label}」？此操作不可撤销。`)) return;
    setErr("");
    try {
      await deleteTemplate(t.key);
      await refresh();
    } catch (e: any) {
      setErr(e?.message || "删除失败");
    }
  };

  if (!open) return null;

  // Unsaved template edits: the editor holds prompt text the user typed.
  const dirty = mode !== "list" && (
    form.key !== EMPTY.key || form.label !== EMPTY.label
    || form.description !== EMPTY.description || form.prompt !== EMPTY.prompt
    || form.category !== EMPTY.category || form.output !== EMPTY.output
    || form.keywords !== EMPTY.keywords || form.timeout !== EMPTY.timeout
    || form.language !== EMPTY.language || form.scheduleType !== EMPTY.scheduleType
  );

  return (
    <Modal open={open} onClose={onClose} title="Prompt 模板库" dirty={dirty}
           className="w-full max-w-5xl max-h-[88vh]">
        <div className="px-4 py-3 border-b border-border flex items-center gap-2">
          <span className="font-medium text-sm">Prompt 模板库</span>
          <span className="text-xs text-text-muted">
            共 {templates.length} 个（内置 {templates.filter((t) => t.builtin).length} 个）
          </span>
          <button type="button" onClick={onClose} className="ml-auto text-sm text-text-muted hover:text-foreground">
            关闭
          </button>
        </div>

        <div className="px-4 py-3 overflow-auto space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={startCreate} className="btn btn-primary text-xs">
              + 新建模板
            </button>
            <select
              className="input max-w-[240px] text-xs"
              value=""
              onChange={(e) => importFromJob(e.target.value)}
            >
              <option value="">从现有 job 导入…</option>
              {jobs.map((j) => (
                <option key={j} value={j}>
                  {j}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-text-muted">
              导入后可改造成通用模板；创建 job 时只需替换 name / keywords
            </span>
          </div>

          {msg && <div className="text-xs text-success">{msg}</div>}
          {err && <div className="text-xs text-danger">{err}</div>}

          {mode !== "list" ? (
            <div className="border border-border rounded-md p-3 space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">
                  {editing ? `编辑模板：${editing.key}` : "新建模板"}
                </span>
                {editing?.builtin && (
                  <span className="badge border border-border text-[10px] text-text-muted">内置（可改不可删）</span>
                )}
                <button
                  type="button"
                  onClick={() => { setMode("list"); setEditing(null); setForm(EMPTY); setErr(""); }}
                  className="ml-auto text-xs text-text-muted hover:text-foreground"
                >
                  取消
                </button>
              </div>

              <div className="grid sm:grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">模板名称 *</label>
                  <input
                    className="input"
                    value={form.label}
                    placeholder="例：竞品动态周报"
                    onChange={(e) => setForm({ ...form, label: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">
                    key（文件名）{editing ? "" : "*"}
                  </label>
                  <input
                    className="input font-mono text-xs"
                    value={form.key}
                    disabled={!!editing}
                    placeholder="competitor-weekly"
                    onChange={(e) => setForm({ ...form, key: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">分类</label>
                  <select
                    className="input"
                    value={form.category}
                    onChange={(e) => setForm({ ...form, category: e.target.value })}
                  >
                    {CATEGORIES.map((c) => (
                      <option key={c} value={c}>
                        {categoryLabel(c)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">一句话描述</label>
                  <input
                    className="input"
                    value={form.description}
                    placeholder="什么时候该用它"
                    onChange={(e) => setForm({ ...form, description: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">默认关键词（逗号分隔）</label>
                  <input
                    className="input"
                    value={form.keywords}
                    placeholder="ai, agent, llm"
                    onChange={(e) => setForm({ ...form, keywords: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">输出路径模板</label>
                  <input
                    className="input font-mono text-xs"
                    value={form.output}
                    placeholder="output/research/{date}_{name}.md"
                    onChange={(e) => setForm({ ...form, output: e.target.value })}
                  />
                </div>
                <div>
                  <label className="block text-xs text-text-muted mb-0.5">超时（秒）</label>
                  <input
                    className="input"
                    value={form.timeout}
                    onChange={(e) => setForm({ ...form, timeout: e.target.value })}
                  />
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="block text-xs text-text-muted mb-0.5">建议频率</label>
                    <select
                      className="input"
                      value={form.scheduleType}
                      onChange={(e) => setForm({ ...form, scheduleType: e.target.value })}
                    >
                      {SCHEDULE_TYPES.map((s) => (
                        <option key={s} value={s}>
                          {s === "manual" ? "手动" : s}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs text-text-muted mb-0.5">时间</label>
                    <input
                      className="input"
                      value={form.scheduleTime}
                      placeholder="08:00"
                      onChange={(e) => setForm({ ...form, scheduleTime: e.target.value })}
                    />
                  </div>
                </div>
              </div>

              <div>
                <label className="block text-xs text-text-muted mb-0.5">
                  Prompt 模板 * <span className="opacity-70">={"{name} {keywords} {date} {date_1d_ago} {date_7d_ago} {language} {recent_outcomes} {reported_events}"}</span>
                </label>
                <textarea
                  className="input font-mono text-xs min-h-[240px] leading-relaxed"
                  value={form.prompt}
                  placeholder={"你是 {name} 领域的分析师…\n\n## 证据规则\n每条发现必须含发布日期与来源 URL…"}
                  onChange={(e) => setForm({ ...form, prompt: e.target.value })}
                />
                <p className="text-[11px] text-text-muted/80 mt-1">
                  建议包含：角色与读者、任务与时间窗、证据规则（日期 + URL，多源交叉验证）、固定输出结构、质量红线、忽略清单。
                </p>
              </div>

              <div className="flex gap-2">
                <button type="button" onClick={save} disabled={busy} className="btn btn-primary text-xs">
                  {busy ? "保存中..." : editing ? "保存修改" : "创建模板"}
                </button>
              </div>
            </div>
          ) : null}

          <TemplateGrid
            templates={templates}
            onEdit={startEdit}
            onDelete={remove}
            onSelect={
              onUse
                ? (t: JobTemplate) => {
                    onUse(t);
                    onClose();
                  }
                : undefined
            }
            emptyHint="还没有模板，点「+ 新建模板」或从现有 job 导入。"
          />
        </div>
    </Modal>
  );
}
