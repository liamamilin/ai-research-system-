import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs, createJob, listTemplates, deleteJob, listJobCategories, type JobCategory, type JobTemplate } from "@/api";
import type { JobSummary } from "@/api/types";
import { useAuthStore } from "@/lib/auth-store";
import { cn, timeAgo } from "@/lib/utils";
import { TemplateGrid } from "@/components/TemplateGrid";
import { ErrorState } from "@/components/ErrorState";
import { errorMessage } from "@/lib/toast";
import { TemplateLibrary } from "@/components/TemplateLibrary";

function normalizeCategory(value: string): string {
  return value.trim().toLowerCase().replace(/^[\s/]+|[\s/]+$/g, "");
}

function StatusBadge({ status }: { status: string | null }) {
  if (!status) return <span className="text-text-muted">—</span>;
  const colors: Record<string, string> = {
    success: "bg-green-900/40 text-green-400 border-green-800",
    failed: "bg-red-900/40 text-red-400 border-red-800",
    skipped: "bg-yellow-900/40 text-yellow-400 border-yellow-800",
  };
  return (
    <span className={cn("badge border", colors[status] || "bg-gray-800 text-gray-400")}>
      {status}
    </span>
  );
}

export function JobsListPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [categories, setCategories] = useState<JobCategory[]>([]);
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  // Create form state
  const [showCreate, setShowCreate] = useState(false);
  const [showLibrary, setShowLibrary] = useState(false);
  const [templates, setTemplates] = useState<JobTemplate[]>([]);
  const [creating, setCreating] = useState(false);
  const [createMsg, setCreateMsg] = useState("");
  const [form, setForm] = useState({
    name: "", template: "", category: "", description: "",
    language: "zh", keywords: "", prompt: "", output: "",
  });

  const fetchTemplates = async () => {
    try {
      const data = await listTemplates();
      setTemplates(data.templates || []);
    } catch {
      setTemplates([]);
    }
  };

  const fetchJobs = async () => {
    setLoading(true);
    setError("");
    try {
      setJobs(await listJobs());
    } catch (err: unknown) {
      setError(errorMessage(err, "无法加载 Job 列表"));
    } finally {
      setLoading(false);
    }
  };

  const fetchCategories = async () => {
    try {
      const data = await listJobCategories();
      setCategories(data.categories || []);
    } catch {
      setCategories([]);
    }
  };

  useEffect(() => { fetchJobs(); fetchCategories(); }, []);

  const openCreate = async () => {
    setShowCreate(true);
    setCreateMsg("");
    fetchTemplates();
  };

  const applyTemplate = (t: JobTemplate) => {
    setForm((f) => ({
      ...f,
      template: t.filename,
      prompt: t.prompt || f.prompt,
      output: t.output_template || f.output,
      language: t.language || f.language,
      description: f.description || t.description,
      keywords: f.keywords || (t.keywords || []).join(", "),
      category: f.category || t.category,
    }));
  };

  const handleDelete = async (jobName: string) => {
    if (!window.confirm(`删除 job '${jobName}' 的 YAML 文件？此操作不可撤销。`)) return;
    try {
      await deleteJob(jobName);
      fetchJobs();
    } catch (err: any) {
      window.alert(err?.message || "删除失败");
    }
  };

  const handleCreate = async () => {
    if (!form.name.trim()) { setCreateMsg("请输入 Job 名称"); return; }
    setCreating(true);
    setCreateMsg("");
    try {
      await createJob({
        name: form.name.trim(),
        template: form.template || undefined,
        category: form.category || undefined,
        description: form.description,
        language: form.language,
        keywords: form.keywords.split(",").map((k: string) => k.trim()).filter(Boolean),
        prompt: form.prompt || undefined,
        output: form.output || undefined,
      });
      setShowCreate(false);
      setForm({ name: "", template: "", category: "", description: "", language: "zh", keywords: "", prompt: "", output: "" });
      fetchJobs();
      fetchCategories();
    } catch (err: any) {
      setCreateMsg(err.message || "创建失败");
    } finally {
      setCreating(false);
    }
  };

  const groups = new Map<string, JobSummary[]>();
  for (const j of jobs) {
    const cat = j.category || "(uncategorized)";
    if (!groups.has(cat)) groups.set(cat, []);
    groups.get(cat)!.push(j);
  }

  const filtered = new Map<string, JobSummary[]>();
  for (const [cat, items] of groups) {
    if (categoryFilter && cat !== categoryFilter) continue;
    const match = items.filter(
      (j) => !filter || j.name.toLowerCase().includes(filter.toLowerCase())
    );
    if (match.length) filtered.set(cat, match);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">Jobs</h1>
        <input
          className="input max-w-xs"
          placeholder="搜索..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        {canEdit && (
          <button onClick={() => setShowLibrary(true)} className="btn text-xs" title="管理 Prompt 模板（新建/编辑/删除）">
            模板库
          </button>
        )}
        {canEdit && (
          <button onClick={openCreate} className="btn btn-primary text-xs">
            + 新建
          </button>
        )}
      </div>

      {/* Category filter */}
      {categories.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <button
            onClick={() => setCategoryFilter("")}
            className={cn("badge border cursor-pointer hover:bg-bg-hover",
              !categoryFilter ? "bg-accent text-white border-accent" : "bg-bg-card border-border")}
          >
            全部分类
          </button>
          {categories.map((cat) => (
            <button
              key={cat.name}
              onClick={() => setCategoryFilter(categoryFilter === cat.name ? "" : cat.name)}
              className={cn("badge border cursor-pointer hover:bg-bg-hover",
                categoryFilter === cat.name
                  ? "bg-accent text-white border-accent"
                  : "bg-bg-card border-border")}
              title={`${cat.name}：${cat.count} 个 job`}
            >
              {cat.name} <span className="opacity-60">{cat.count}</span>
            </button>
          ))}
        </div>
      )}

      {/* Create form */}
      {showCreate && (
        <div className="card p-4 space-y-3">
          <h2 className="text-sm font-medium">新建 Job</h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-text-muted mb-0.5">
                名称 *<span className="ml-1 opacity-70">= Prompt 里的 {"{name}"}</span>
              </label>
              <input className="input" placeholder="如: My Research" value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-0.5">分类（子目录）</label>
              <input
                className="input"
                list="job-categories"
                placeholder="选择已有分类或输入新分类"
                value={form.category}
                onChange={(e) => setForm({ ...form, category: normalizeCategory(e.target.value) })}
              />
              <datalist id="job-categories">
                {categories.map((cat) => (
                  <option key={cat.name} value={cat.name}>{cat.count} 个 job</option>
                ))}
              </datalist>
              {categories.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-1.5">
                  {categories.map((cat) => (
                    <button
                      key={cat.name}
                      type="button"
                      onClick={() => setForm({ ...form, category: cat.name })}
                      title={`使用分类 ${cat.name}`}
                      className={cn("badge border cursor-pointer hover:bg-bg-hover transition",
                        form.category === cat.name
                          ? "bg-accent text-white border-accent"
                          : "bg-bg-card border-border")}
                    >
                      {cat.name} <span className="opacity-60">{cat.count}</span>
                    </button>
                  ))}
                </div>
              )}
              {form.category && !categories.some((c) => c.name === form.category) && (
                <p className="text-[11px] text-warning mt-1">
                  将新建分类「{form.category}」— 确认没有拼写错误，或改用上方已有分类
                </p>
              )}
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs text-text-muted mb-0.5">描述</label>
              <input className="input" placeholder="可选" value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-0.5">语言</label>
              <select className="input" value={form.language}
                onChange={(e) => setForm({ ...form, language: e.target.value })}>
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </div>
            <div>
              <label className="block text-xs text-text-muted mb-0.5">
                关键词（逗号分隔）<span className="ml-1 opacity-70">= {"{keywords}"}</span>
              </label>
              <input className="input" placeholder="AI, agents" value={form.keywords}
                onChange={(e) => setForm({ ...form, keywords: e.target.value })} />
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs text-text-muted mb-0.5">
                Prompt <span className="ml-1 opacity-70">（必填，可用模板变量）</span>
              </label>
              <textarea className="input font-mono text-xs min-h-[80px]" placeholder="Research {name} with keywords: {keywords}..."
                value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
              <p className="text-[11px] text-text-muted/80 mt-1">
                可用变量：{"{name}"} {"{keywords}"} {"{language}"} {"{date}"} {"{date_1d_ago}"}{" "}
                {"{date_7d_ago}"} {"{time}"} {"{datetime}"} {"{recent_outcomes}"}（上一轮已判定结果）{"{reported_events}"}（已报道来源）；
                输出路径模板支持 {"{name}"} {"{date}"} {"{time}"} {"{datetime}"}。建议写清：角色、目标、输出结构、每条发现的证据要求（发布日期 + 来源 URL）。
              </p>
            </div>
            <div className="sm:col-span-2">
              <label className="block text-xs text-text-muted mb-0.5">输出路径模板（可选）</label>
              <input className="input font-mono text-xs" placeholder="output/{date}_{name}.md"
                value={form.output} onChange={(e) => setForm({ ...form, output: e.target.value })} />
            </div>
            <div className="sm:col-span-2">
              <div className="flex items-center gap-2 mb-1">
                <label className="block text-xs text-text-muted">从模板开始（可选）</label>
                <button type="button" onClick={() => setForm({ ...form, template: "" })}
                  className={cn("px-2 py-0.5 text-[11px] rounded border transition",
                    !form.template ? "bg-accent text-white border-accent" : "bg-bg-card border-border hover:bg-bg-hover")}>
                  不用模板
                </button>
                <button type="button" onClick={() => setShowLibrary(true)}
                  className="text-[11px] text-accent hover:underline">
                  管理/新建模板 →
                </button>
                {form.template && (
                  <span className="text-[11px] text-text-muted">已选：{form.template}</span>
                )}
              </div>
              <div className="border border-border rounded-md p-2 max-h-[240px] overflow-auto">
                <TemplateGrid
                  templates={templates}
                  selectedKey={
                    templates.find((t) => t.filename === form.template)?.key
                  }
                  onSelect={applyTemplate}
                  compact
                  emptyHint="暂无模板，点「管理/新建模板」创建，或直接手写 Prompt。"
                />
              </div>
              <p className="text-[11px] text-text-muted/80 mt-1">
                选中模板会把它的 prompt / 输出路径 / 关键词填入下方表单，接下来只需改 <code>name</code> 与 <code>keywords</code>。
              </p>
            </div>
          </div>

          {createMsg && <div className="text-sm text-danger">{createMsg}</div>}
          <div className="flex gap-2">
            <button onClick={handleCreate} disabled={creating} className="btn btn-primary">
              {creating ? "创建中..." : "创建"}
            </button>
            <button onClick={() => setShowCreate(false)} className="btn">取消</button>
          </div>
        </div>
      )}

      {/* Job list */}
      {loading ? (
        <div className="text-sm text-text-muted">加载中...</div>
      ) : error ? (
        <ErrorState error={error} onRetry={fetchJobs} />
      ) : jobs.length === 0 ? (
        <div className="card p-8 text-center text-sm text-text-muted space-y-2">
          <div>还没有任何 Job</div>
          {canEdit && <div className="text-xs">点右上角「+ 新建」，可以从模板库创建第一个 Job。</div>}
        </div>
      ) : (
        <div className="space-y-4">
          {[...filtered.entries()].map(([cat, items]) => (
            <div key={cat} className="card">
              <div className="px-4 py-2 border-b border-border text-xs text-text-muted uppercase">
                {cat}
              </div>
              <div className="divide-y divide-border">
                {items.map((j) => (
                  <Link
                    key={j.name}
                    to={`/jobs/${encodeURIComponent(j.name)}`}
                    className="flex items-center gap-3 px-4 py-2.5 hover:bg-bg-hover transition text-sm"
                  >
                    <div
                      className={cn(
                        "w-2 h-2 rounded-full shrink-0",
                        j.enabled ? "bg-success" : "bg-text-muted"
                      )}
                      title={j.enabled ? "enabled" : "disabled"}
                    />
                    <span className="font-mono flex-1 truncate">{j.name}</span>
                    {j.is_running && (
                      <span className="inline-flex items-center gap-1 text-xs text-accent shrink-0">
                        <span className="w-2 h-2 rounded-full bg-accent animate-pulse" />
                        运行中
                      </span>
                    )}
                    <StatusBadge status={j.state?.last_status || null} />
                    {j.state?.last_run_at && (
                      <span className="text-text-muted text-xs shrink-0" title={j.state.last_run_at}>
                        {timeAgo(j.state.last_run_at)}
                      </span>
                    )}
                    {canEdit && (
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(j.name); }}
                        className="text-text-muted hover:text-danger text-xs px-1 shrink-0"
                        title="删除 job"
                      >
                        ✕
                      </button>
                    )}
                    {(j.keywords || []).length > 0 && (
                      <span className="hidden lg:flex gap-1">
                        {(j.keywords || []).slice(0, 2).map((kw: string) => (
                          <span key={kw} className="badge bg-bg-hover border border-border">
                            {kw}
                          </span>
                        ))}
                      </span>
                    )}
                  </Link>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {canEdit && (
        <TemplateLibrary
          open={showLibrary}
          onClose={() => {
            setShowLibrary(false);
            fetchTemplates();
          }}
          onUse={(t) => {
            setShowCreate(true);
            applyTemplate(t);
          }}
        />
      )}
    </div>
  );
}
