import { useEffect, useState, useCallback, useRef } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { listReports, getReportTree, searchReports, getReportCategories, listTags, updateReportMeta } from "@/api";
import { cn, formatTokens } from "@/lib/utils";
import { ErrorState, RoleGate } from "@/components/ErrorState";
import { errorMessage, useToast } from "@/lib/toast";
import { Search, FileText, FolderOpen, Star } from "lucide-react";

const UI_STATE_KEY = "ai-research-console:reports-ui";

interface TreeUiState {
  treeOpen: boolean;
  treeScroll: number;
  openDirs: string[] | null;
  lastVisited: string | null;
}

function loadTreeUiState(): TreeUiState {
  const fallback: TreeUiState = {
    treeOpen: true, treeScroll: 0, openDirs: null, lastVisited: null,
  };
  try {
    const raw = window.sessionStorage.getItem(UI_STATE_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw);
    return {
      treeOpen: typeof saved.treeOpen === "boolean" ? saved.treeOpen : true,
      treeScroll: typeof saved.treeScroll === "number" ? saved.treeScroll : 0,
      openDirs: Array.isArray(saved.openDirs) ? saved.openDirs : null,
      lastVisited: typeof saved.lastVisited === "string" ? saved.lastVisited : null,
    };
  } catch {
    return fallback;
  }
}

/** Render an FTS5 snippet. Only the backend's <mark> tags are honored;
 *  everything else is escaped by React (no raw HTML injection). */
function Snippet({ html }: { html: string }) {
  const parts = html.split(/(<mark>[\s\S]*?<\/mark>)/g);
  return (
    <span className="block w-full mt-1 text-xs text-text-muted/70 line-clamp-2">
      {parts.map((part, i) =>
        part.startsWith("<mark>") && part.endsWith("</mark>") ? (
          <mark key={i} className="bg-accent/30 text-text rounded-sm px-0.5">
            {part.slice(6, -7)}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </span>
  );
}

/** Page numbers to display: first/last plus a window around the current page. */
function pageWindow(page: number, pages: number, span = 2): (number | "…")[] {
  const wanted = new Set<number>([1, pages]);
  for (let p = page - span; p <= page + span; p++) {
    if (p >= 1 && p <= pages) wanted.add(p);
  }
  const sorted = [...wanted].sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (prev && p - prev > 1) out.push("…");
    out.push(p);
    prev = p;
  }
  return out;
}

export function ReportsPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Report list state
  const [reports, setReports] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [pages, setPages] = useState(1);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);

  // Tree state (browsing position is remembered across navigation)
  const [tree, setTree] = useState<any[]>([]);
  const [ui, setUi] = useState<TreeUiState>(() => loadTreeUiState());
  const { treeOpen, treeScroll, openDirs, lastVisited } = ui;
  const treeRef = useRef<HTMLDivElement | null>(null);
  const restoredRef = useRef(false);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(UI_STATE_KEY, JSON.stringify(ui));
    } catch {
      /* storage unavailable */
    }
  }, [ui]);

  const patchUi = (patch: Partial<TreeUiState>) =>
    setUi((prev) => ({ ...prev, ...patch }));

  const toggleDir = (name: string) => {
    setUi((prev) => {
      const current = prev.openDirs ?? [];
      return {
        ...prev,
        openDirs: current.includes(name)
          ? current.filter((d) => d !== name)
          : [...current, name],
      };
    });
  };

  // First visit: expand top-level directories, then remember the explicit set
  useEffect(() => {
    if (tree.length === 0 || ui.openDirs !== null) return;
    setUi((prev) => (prev.openDirs === null
      ? { ...prev, openDirs: tree.filter((e) => e.type === "dir").map((e) => e.name) }
      : prev));
  }, [tree, ui.openDirs]);

  // Restore the scroll position once the tree content is rendered
  useEffect(() => {
    if (restoredRef.current || tree.length === 0 || !treeRef.current) return;
    restoredRef.current = true;
    if (treeScroll > 0) treeRef.current.scrollTop = treeScroll;
    if (lastVisited) {
      const nodes = Array.from(
        treeRef.current.querySelectorAll<HTMLElement>("[data-path]"),
      );
      const target = nodes.find((el) => el.dataset.path === lastVisited);
      if (target && typeof target.scrollIntoView === "function") {
        target.scrollIntoView({ block: "nearest" });
      }
    }
  }, [tree, treeScroll, lastVisited]);

  const openReport = (path: string) => {
    patchUi({ lastVisited: path });
    navigate(`/reports/view?path=${encodeURIComponent(path)}`);
  };

  // Search state
  const [query, setQuery] = useState(searchParams.get("q") || "");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searchTotal, setSearchTotal] = useState(0);
  const [searching, setSearching] = useState(false);

  const [error, setError] = useState("");
  const toast = useToast();
  const [category, setCategory] = useState("");
  const [categories, setCategories] = useState<string[]>([]);
  const [scope, setScope] = useState<"all" | "fav" | "unread">("all");
  const [tag, setTag] = useState("");
  const [tags, setTags] = useState<{ tag: string; count: number }[]>([]);

  const fetchReports = useCallback(async (
    pageNum: number,
    cat?: string,
    scopeArg?: "all" | "fav" | "unread",
    tagArg?: string,
  ) => {
    const activeScope = scopeArg ?? scope;
    const activeTag = tagArg ?? tag;
    setLoading(true);
    try {
      const data = await listReports({
        page: pageNum,
        per_page: 20,
        category: cat || undefined,
        favorite: activeScope === "fav" ? true : undefined,
        unread: activeScope === "unread" ? true : undefined,
        tag: activeTag || undefined,
      });
      setReports(data.items);
      setTotal(data.total);
      setPages(data.pages);
      setError("");
    } catch (err: unknown) {
      setError(errorMessage(err, "无法加载报告列表"));
      setReports([]);
    } finally {
      setLoading(false);
    }
  }, [scope, tag]);

  const fetchTree = useCallback(async () => {
    try {
      const [treeData, cats, tagData] = await Promise.all([
        getReportTree(),
        getReportCategories(),
        listTags().catch(() => ({ tags: [] })),
      ]);
      setTree(treeData);
      setCategories(cats);
      setTags(tagData.tags || []);
      setError("");
    } catch (err: unknown) {
      setError(errorMessage(err, "无法加载目录与分类"));
      setTree([]);
      setCategories([]);
    }
  }, []);

  const handleScope = (next: "all" | "fav" | "unread") => {
    setScope(next);
    setPage(1);
    fetchReports(1, category, next);
  };

  const handleTagFilter = (next: string) => {
    const value = tag === next ? "" : next;
    setTag(value);
    setPage(1);
    fetchReports(1, category, scope, value);
  };

  const toggleFavorite = async (item: any) => {
    const next = !item.favorite;
    setReports((prev) => prev.map((r) => (r.path === item.path ? { ...r, favorite: next } : r)));
    try {
      await updateReportMeta(item.path, { favorite: next });
      if (scope === "fav" && !next) fetchReports(page, category, scope);
    } catch (err: unknown) {
      setReports((prev) => prev.map((r) => (r.path === item.path ? { ...r, favorite: !next } : r)));
      toast.error("星标未保存", errorMessage(err));
    }
  };

  useEffect(() => {
    fetchTree();
    fetchReports(1);
  }, [fetchTree, fetchReports]);

  const handleSearch = async () => {
    if (!query.trim()) {
      fetchReports(1, category);
      setSearchResults([]);
      setSearchTotal(0);
      return;
    }
    setSearching(true);
    try {
      const data = await searchReports(query.trim(), 50);
      setSearchResults(data.results);
      setSearchTotal(data.total);
    } catch {
      setSearchResults([]);
      setSearchTotal(0);
    } finally {
      setSearching(false);
    }
    setSearchParams({ q: query });
  };

  const handleCategoryFilter = (cat: string) => {
    setCategory(cat);
    setPage(1);
    fetchReports(1, cat);
  };

  const isSearching = query.trim().length > 0;
  const displayResults = isSearching ? searchResults : reports;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">报告</h1>
        {!isSearching && total > 0 && (
          <span className="text-xs text-text-muted">共 {total} 篇</span>
        )}
      </div>

      {/* Search bar */}
      <div className="flex gap-2">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
          <input
            className="input pl-9"
            placeholder="搜索报告全文..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSearch()}
          />
        </div>
        <button onClick={handleSearch} disabled={searching} className="btn btn-primary">
          {searching ? "搜索中..." : "搜索"}
        </button>
      </div>

      <div className="flex gap-4">
        {/* Tree sidebar */}
        <div className="hidden lg:block w-56 shrink-0">
          <div className="card">
            <button
              onClick={() => patchUi({ treeOpen: !treeOpen })}
              className="flex items-center gap-2 w-full px-3 py-2 text-xs text-text-muted uppercase"
            >
              <FolderOpen className="w-3 h-3" />
              目录
            </button>
            {treeOpen && (
              <div
                ref={treeRef}
                onScroll={(e) => patchUi({ treeScroll: e.currentTarget.scrollTop })}
                className="px-2 pb-2 text-xs max-h-[70vh] overflow-y-auto space-y-0.5"
              >
                {tree.map((entry) => (
                  <TreeEntry
                    key={entry.name}
                    entry={entry}
                    depth={0}
                    openDirs={openDirs ?? []}
                    activePath={lastVisited}
                    onToggleDir={toggleDir}
                    onSelect={openReport}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          {/* Scope + tag filters */}
          {!isSearching && (
            <div className="flex flex-wrap items-center gap-1.5 mb-3">
              {([["all", "全部"], ["fav", "星标"], ["unread", "未读"]] as const).map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => handleScope(key)}
                  className={cn("badge border cursor-pointer hover:bg-bg-hover",
                    scope === key ? "bg-accent text-white border-accent" : "bg-bg-card border-border"
                  )}
                >
                  {label}
                </button>
              ))}
              {tags.length > 0 && <span className="text-border px-1">|</span>}
              {tags.slice(0, 12).map((t) => (
                <button
                  key={t.tag}
                  onClick={() => handleTagFilter(t.tag)}
                  className={cn("badge border cursor-pointer hover:bg-bg-hover",
                    tag === t.tag ? "bg-accent text-white border-accent" : "bg-bg-card border-border"
                  )}
                >
                  #{t.tag} <span className="opacity-60">{t.count}</span>
                </button>
              ))}
            </div>
          )}

          {/* Category pills */}
          {!isSearching && (
            <div className="flex flex-wrap gap-1.5 mb-3">
              <button
                onClick={() => handleCategoryFilter("")}
                className={cn("badge border cursor-pointer hover:bg-bg-hover",
                  !category ? "bg-accent text-white border-accent" : "bg-bg-card border-border"
                )}
              >
                全部
              </button>
              {categories.map((c) => (
                <button
                  key={c}
                  onClick={() => handleCategoryFilter(c)}
                  className={cn("badge border cursor-pointer hover:bg-bg-hover",
                    category === c ? "bg-accent text-white border-accent" : "bg-bg-card border-border"
                  )}
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          {/* Results */}
          {isSearching && (
            <p className="text-sm text-text-muted mb-2">
              搜索 "{query}" — {searchTotal} 条结果
            </p>
          )}

          {loading || searching ? (
            <div className="text-sm text-text-muted">加载中...</div>
          ) : error ? (
            <ErrorState
              error={error}
              onRetry={() => fetchReports(page, category, scope, tag)}
              empty="没有报告"
            />
          ) : displayResults.length === 0 ? (
            <div className="card p-6 text-center text-text-muted text-sm">暂无报告</div>
          ) : (
            <div className="space-y-2">
              {displayResults.map((item: any) => (
                <div
                  key={item.path}
                  className="card px-4 py-3 hover:bg-bg-hover cursor-pointer transition"
                  onClick={() => openReport(item.path)}
                >
                  <div className="flex items-start gap-3">
                    <FileText className="w-4 h-4 mt-0.5 shrink-0 text-text-muted" />
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium truncate flex items-center gap-2">
                        {!item.read && item.read !== undefined && (
                          <span className="w-1.5 h-1.5 rounded-full bg-accent inline-block shrink-0" title="未读" />
                        )}
                        {item.title || item.path.split("/").pop()}
                        {(item.tags || []).map((t: string) => (
                          <span key={t} className="text-[10px] text-text-muted border border-border rounded px-1 shrink-0">
                            #{t}
                          </span>
                        ))}
                      </div>
                      <div className="text-xs text-text-muted mt-0.5 flex flex-wrap gap-2">
                        <span>{item.path}</span>
                        {item.size != null && (
                          <span>{(item.size / 1024).toFixed(1)} KB</span>
                        )}
                        {item.job_name && <span>· {item.job_name}</span>}
                        {item.meta && (
                          <span className="text-text-muted/80">
                            · {item.meta.model}
                            {item.meta.total_tokens ? ` · ${formatTokens(item.meta.total_tokens)} tok` : ""}
                            {item.meta.duration_seconds ? ` · ${Math.round(item.meta.duration_seconds)}s` : ""}
                            {item.meta.searches ? ` · ${item.meta.searches} 搜索` : ""}
                          </span>
                        )}
                        {item.quality && item.quality.total > 0 && (
                          <span
                            className={cn(
                              "badge border text-[10px]",
                              item.quality.unmatched === 0
                                ? "bg-green-900/40 text-green-400 border-green-800"
                                : item.quality.coverage >= 0.6
                                  ? "bg-yellow-900/40 text-yellow-400 border-yellow-800"
                                  : "bg-red-900/40 text-red-400 border-red-800"
                            )}
                            title={
                              `引用溯源：${item.quality.matched}/${item.quality.total} 来自本次检索` +
                              (item.quality.unmatched ? `，${item.quality.unmatched} 个未在检索结果中` : "")
                            }
                          >
                            引用 {Math.round((item.quality.coverage ?? 0) * 100)}%
                          </span>
                        )}
                        {item.snippet && <Snippet html={String(item.snippet)} />}
                      </div>
                    </div>
                    <RoleGate roles={["editor", "admin"]}>
                      <button
                        onClick={(e) => { e.stopPropagation(); toggleFavorite(item); }}
                        className="p-1 rounded hover:bg-bg-card transition shrink-0"
                        title={item.favorite ? "取消星标" : "加星标"}
                        aria-label={item.favorite ? "取消星标" : "加星标"}
                      >
                        <Star className={cn("w-4 h-4",
                          item.favorite ? "fill-current text-warning" : "text-text-muted")} />
                      </button>
                    </RoleGate>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Pagination */}
          {!isSearching && pages > 1 && (
            <div className="flex justify-center items-center gap-2 mt-4">
              <button
                onClick={() => { setPage(page - 1); fetchReports(page - 1, category); }}
                disabled={page <= 1}
                className="btn btn-sm"
              >
                上一页
              </button>
              {pageWindow(page, pages).map((p, i) =>
                p === "…" ? (
                  <span key={`gap-${i}`} className="text-text-muted text-xs px-1">…</span>
                ) : (
                  <button
                    key={p}
                    onClick={() => { setPage(p); fetchReports(p, category); }}
                    className={cn("btn btn-sm", p === page && "btn-primary")}
                  >
                    {p}
                  </button>
                )
              )}
              <button
                onClick={() => { setPage(page + 1); fetchReports(page + 1, category); }}
                disabled={page >= pages}
                className="btn btn-sm"
              >
                下一页
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --- Tree Entry ---

function TreeEntry({
  entry,
  depth,
  openDirs,
  activePath,
  onToggleDir,
  onSelect,
}: {
  entry: any;
  depth: number;
  openDirs: string[];
  activePath: string | null;
  onToggleDir: (name: string) => void;
  onSelect: (path: string) => void;
}) {
  const open = openDirs.includes(entry.name);
  if (entry.type === "dir") {
    return (
      <div>
        <button
          onClick={() => onToggleDir(entry.name)}
          className="flex items-center gap-1 w-full text-left px-2 py-1 rounded hover:bg-bg-hover"
          style={{ paddingLeft: `${8 + depth * 12}px` }}
        >
          <span className="text-text-muted">{open ? "▾" : "▸"}</span>
          <span className="truncate">{entry.name}</span>
        </button>
        {open && entry.children?.map((child: any) => (
          <TreeEntry
            key={child.name}
            entry={child}
            depth={depth + 1}
            openDirs={openDirs}
            activePath={activePath}
            onToggleDir={onToggleDir}
            onSelect={onSelect}
          />
        ))}
      </div>
    );
  }

  const active = activePath === entry.path;
  return (
    <button
      onClick={() => onSelect(entry.path)}
      data-path={entry.path}
      title={entry.path}
      className={cn(
        "flex items-center gap-1 w-full text-left px-2 py-1 rounded hover:bg-bg-hover text-text-muted",
        active && "bg-accent/20 text-text",
      )}
      style={{ paddingLeft: `${8 + depth * 12}px` }}
    >
      <FileText className="w-3 h-3 shrink-0" />
      <span className="truncate">{entry.title || entry.path.split("/").pop()}</span>
    </button>
  );
}
