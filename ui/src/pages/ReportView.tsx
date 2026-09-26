import { useEffect, useState, useCallback } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { getReportRaw, updateReportMeta, createShareLink, emailReport } from "@/api";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ArrowLeft, Download, Copy, List, Star, Tag, Share2, Mail, FileCode } from "lucide-react";
import { cn, formatTokens } from "@/lib/utils";
import { useAuthStore } from "@/lib/auth-store";
import { errorMessage, useToast } from "@/lib/toast";

interface Heading {
  level: number;
  text: string;
  id: string;
}

function slugify(text: string): string {
  return (
    text
      .trim()
      .toLowerCase()
      .replace(/[*`_~]/g, "")
      .replace(/[^\w\u4e00-\u9fa5]+/g, "-")
      .replace(/^-+|-+$/g, "") || "section"
  );
}

function childrenToText(children: unknown): string {
  if (children == null) return "";
  if (typeof children === "string" || typeof children === "number") {
    return String(children);
  }
  if (Array.isArray(children)) return children.map(childrenToText).join("");
  if (typeof children === "object" && "props" in (children as any)) {
    return childrenToText((children as any).props?.children);
  }
  return "";
}

function extractHeadings(markdown: string): Heading[] {
  const headings: Heading[] = [];
  let inFence = false;
  for (const line of markdown.split("\n")) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*$/.exec(line);
    if (match) {
      headings.push({
        level: match[1].length,
        text: match[2].replace(/[*`_~]/g, ""),
        id: slugify(match[2]),
      });
    }
  }
  return headings;
}

export function ReportViewPage() {
  const [searchParams] = useSearchParams();
  const path = searchParams.get("path") || "";

  const [content, setContent] = useState("");
  const [meta, setMeta] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [tocOpen, setTocOpen] = useState(true);
  const [activeId, setActiveId] = useState("");
  const [indexMeta, setIndexMeta] = useState<any>(null);
  const [notice, setNotice] = useState("");
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";
  const toast = useToast();
  const [busyAction, setBusyAction] = useState("");

  const fetchContent = useCallback(async () => {
    if (!path) {
      setError("缺少 path 参数");
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const data = await getReportRaw(path);
      setContent(data.content);
      setMeta((data as any).meta || null);
      setIndexMeta((data as any).index || null);
      if (canEdit) {
        updateReportMeta(path, { read: true })
          .then((row) => setIndexMeta(row))
          .catch((err: unknown) => {
            toast.warning("未能标记为已读", errorMessage(err));
          });
      }
    } catch (err: any) {
      setError(err.message || "加载失败");
    } finally {
      setLoading(false);
    }
  }, [path, canEdit, toast]);

  const runAction = async (key: string, fn: () => Promise<void>) => {
    setBusyAction(key);
    setNotice("");
    try {
      await fn();
    } catch (err: unknown) {
      setNotice(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusyAction("");
    }
  };

  const handleToggleFavorite = () => runAction("fav", async () => {
    const next = !indexMeta?.favorite;
    const row = await updateReportMeta(path, { favorite: next });
    setIndexMeta(row);
    setNotice(next ? "已加星标" : "已取消星标");
  });

  const handleEditTags = () => runAction("tags", async () => {
    const current = (indexMeta?.tags || []).join(", ");
    const raw = window.prompt("标签（逗号分隔）：", current);
    if (raw === null) return;
    const tags = raw.split(",").map((t) => t.trim()).filter(Boolean);
    const row = await updateReportMeta(path, { tags });
    setIndexMeta(row);
    setNotice("标签已更新");
  });

  const handleShare = () => runAction("share", async () => {
    const link = await createShareLink(path);
    const full = window.location.origin + link.url;
    let copied = false;
    try {
      await navigator.clipboard.writeText(full);
      copied = true;
    } catch {
      toast.error("链接已生成，但复制失败", "浏览器拒绝了剪贴板访问，请手动复制：" + full);
    }
    setNotice(
      copied
        ? "分享链接已复制（7 天有效，未登录可只读访问）"
        : "分享链接已生成（7 天有效），请手动复制上方地址"
    );
  });

  const handleSetRating = (value: number) => runAction("rating", async () => {
    const next = indexMeta?.rating === value ? 0 : value;
    const row = await updateReportMeta(path, { rating: next });
    setIndexMeta(row);
    setNotice(next ? `已评分 ${next}/5` : "已清除评分");
  });

  const handleExportHtml = () => {
    // noopener: without it the exported document gets a live handle on this
    // window, and the body is model output rather than something we authored.
    window.open(
      `/api/reports/html?path=${encodeURIComponent(path)}&download=true`,
      "_blank",
      "noopener",
    );
  };

  const handleEmail = () => runAction("email", async () => {
    const raw = window.prompt("收件人（逗号分隔，留空使用默认）：", "");
    if (raw === null) return;
    const to = raw.split(",").map((t) => t.trim()).filter(Boolean);
    await emailReport(path, to.length ? to : undefined);
    setNotice("邮件已发送");
  });

  useEffect(() => {
    fetchContent();
  }, [fetchContent]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  const handleDownload = () => {
    const blob = new Blob([content], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop() || "report.md";
    a.click();
    URL.revokeObjectURL(url);
  };

  if (loading) {
    return <div className="text-text-muted">加载中...</div>;
  }

  if (error) {
    return (
      <div className="space-y-4">
        <Link to="/reports" className="btn">
          <ArrowLeft className="w-4 h-4" />
          返回
        </Link>
        <div className="card p-4 text-danger">{error}</div>
      </div>
    );
  }

  const filename = path.split("/").pop() || "";
  const headings = content ? extractHeadings(content) : [];

  return (
    <div className="space-y-4">
      {/* Toolbar */}
      <div className="flex items-center gap-2 text-sm">
        <Link to="/reports" className="text-text-muted hover:text-text">
          <ArrowLeft className="w-4 h-4 inline mr-1" />
          报告
        </Link>
        <span className="text-text-muted">/</span>
        <span className="font-mono flex-1 truncate">{filename}</span>
        {headings.length > 2 && (
          <button onClick={() => setTocOpen(!tocOpen)} className="btn text-xs" title="目录">
            <List className="w-3 h-3" />
            目录
          </button>
        )}
        {canEdit && (
        <span className="flex items-center gap-0.5" title="报告评分（点击设置，再点取消）">
          {[1, 2, 3, 4, 5].map((value) => (
            <button
              key={value}
              onClick={() => handleSetRating(value)}
              disabled={!!busyAction}
              className="p-0.5 rounded hover:bg-bg-hover transition"
              title={`${value} 分`}
            >
              <Star className={cn("w-3.5 h-3.5",
                (indexMeta?.rating || 0) >= value
                  ? "fill-current text-warning"
                  : "text-text-muted")} />
            </button>
          ))}
        </span>
        )}
        {canEdit && (
        <>
        <button onClick={handleToggleFavorite} disabled={!!busyAction}
                className="btn text-xs" title="星标">
          <Star className={cn("w-3 h-3", indexMeta?.favorite && "fill-current text-warning")} />
          星标
        </button>
        <button onClick={handleEditTags} disabled={!!busyAction} className="btn text-xs" title="编辑标签">
          <Tag className="w-3 h-3" />
          标签
        </button>
        <button onClick={handleShare} disabled={!!busyAction} className="btn text-xs" title="生成分享链接">
          <Share2 className="w-3 h-3" />
          {busyAction === "share" ? "生成中..." : "分享"}
        </button>
        </>
        )}
        <button onClick={handleExportHtml} className="btn text-xs" title="导出 HTML">
          <FileCode className="w-3 h-3" />
          HTML
        </button>
        {canEdit && (
          <button onClick={handleEmail} disabled={!!busyAction} className="btn text-xs" title="邮件发送">
            <Mail className="w-3 h-3" />
            {busyAction === "email" ? "发送中..." : "邮件"}
          </button>
        )}
        <button onClick={handleCopy} className="btn text-xs" title="复制原文">
          <Copy className="w-3 h-3" />
          {copied ? "已复制" : "复制"}
        </button>
        <button onClick={handleDownload} className="btn text-xs" title="下载 Markdown">
          <Download className="w-3 h-3" />
          下载
        </button>
      </div>

      {notice && (
        <div className="text-xs text-text-muted bg-bg-card border border-border rounded-md px-3 py-1.5">
          {notice}
        </div>
      )}

      {/* Breadcrumb path + metadata */}
      <div className="text-xs text-text-muted flex flex-wrap items-center gap-2">
        <span>{path}</span>
        {meta && (
          <span className="text-text-muted/80">
            · {meta.model}
            {meta.total_tokens ? ` · ${formatTokens(meta.total_tokens)} tokens` : ""}
            {meta.duration_seconds ? ` · ${Math.round(meta.duration_seconds)}s` : ""}
            {meta.searches ? ` · ${meta.searches} 次搜索` : ""}
            {meta.ts ? ` · ${String(meta.ts).slice(0, 19).replace("T", " ")}` : ""}
          </span>
        )}
        {meta?.citation_check?.total > 0 && (
          <span
            className={cn(
              "badge border",
              meta.citation_check.unmatched === 0
                ? "bg-green-900/40 text-green-400 border-green-800"
                : meta.citation_check.coverage >= 0.8
                  ? "bg-yellow-900/40 text-yellow-400 border-yellow-800"
                  : "bg-red-900/40 text-red-400 border-red-800"
            )}
            title={
              meta.citation_check.unmatched_examples?.length
                ? `未在本次检索结果中的引用：\n${meta.citation_check.unmatched_examples.join("\n")}`
                : "报告中所有 URL 都来自本次检索"
            }
          >
            引用 {meta.citation_check.matched}/{meta.citation_check.total} 可追溯
            {meta.citation_check.unmatched > 0 && `（${meta.citation_check.unmatched} 个存疑）`}
          </span>
        )}
      </div>

      <div className="flex gap-4 items-start">
        {tocOpen && headings.length > 2 && (
          <nav className="hidden xl:block w-64 shrink-0 sticky top-4">
            <div className="card p-3 max-h-[80vh] overflow-y-auto">
              <div className="text-xs text-text-muted uppercase mb-2">目录</div>
              <div className="space-y-0.5">
                {headings.map((h, i) => (
                  <button
                    key={`${h.id}-${i}`}
                    onClick={() => {
                      document.getElementById(h.id)?.scrollIntoView({ behavior: "smooth", block: "start" });
                      setActiveId(h.id);
                    }}
                    className={cn(
                      "block w-full text-left text-xs rounded px-1.5 py-1 hover:bg-bg-hover truncate",
                      activeId === h.id ? "text-accent" : "text-text-muted"
                    )}
                    style={{ paddingLeft: `${6 + (h.level - 1) * 12}px` }}
                    title={h.text}
                  >
                    {h.text}
                  </button>
                ))}
              </div>
            </div>
          </nav>
        )}

        {/* Markdown content */}
        <div className="card p-4 md:p-6 overflow-x-auto flex-1 min-w-0">
          <div className="md-body md-body-wide">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children, ...props }) => (
                <h1 id={slugify(childrenToText(children))} className="text-xl font-bold mt-6 mb-3 pb-1 border-b border-border scroll-mt-4" {...props}>
                  {children}
                </h1>
              ),
              h2: ({ children, ...props }) => (
                <h2 id={slugify(childrenToText(children))} className="text-lg font-semibold mt-5 mb-2 scroll-mt-4" {...props}>
                  {children}
                </h2>
              ),
              h3: ({ children, ...props }) => (
                <h3 id={slugify(childrenToText(children))} className="text-base font-medium mt-4 mb-1 scroll-mt-4" {...props}>
                  {children}
                </h3>
              ),
              p: ({ children, ...props }) => (
                <p className="my-1.5" {...props}>
                  {children}
                </p>
              ),
              ul: ({ children, ...props }) => (
                <ul className="list-disc pl-5 my-1.5 space-y-0.5" {...props}>
                  {children}
                </ul>
              ),
              ol: ({ children, ...props }) => (
                <ol className="list-decimal pl-5 my-1.5 space-y-0.5" {...props}>
                  {children}
                </ol>
              ),
              li: ({ children, ...props }) => (
                <li className="my-0.5" {...props}>
                  {children}
                </li>
              ),
              code: ({ children, className, ...props }) => {
                const isInline = !className;
                if (isInline) {
                  return (
                    <code
                      className="bg-bg-hover px-1 py-0.5 rounded text-xs font-mono"
                      {...props}
                    >
                      {children}
                    </code>
                  );
                }
                return (
                  <pre className="bg-[#0d1117] p-3 rounded-md overflow-x-auto my-2">
                    <code className="text-xs font-mono leading-relaxed" {...props}>
                      {children}
                    </code>
                  </pre>
                );
              },
              pre: ({ children }) => <>{children}</>,
              table: ({ children, ...props }) => (
                <div className="overflow-x-auto my-3">
                  <table className="min-w-full text-xs border-collapse" {...props}>
                    {children}
                  </table>
                </div>
              ),
              th: ({ children, ...props }) => (
                <th className="border border-border px-2 py-1 bg-bg-hover text-left font-medium" {...props}>
                  {children}
                </th>
              ),
              td: ({ children, ...props }) => (
                <td className="border border-border px-2 py-1" {...props}>
                  {children}
                </td>
              ),
              a: ({ children, href, ...props }) => (
                <a
                  className="text-accent hover:underline"
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer"
                  {...props}
                >
                  {children}
                </a>
              ),
              blockquote: ({ children, ...props }) => (
                <blockquote className="border-l-2 border-accent pl-3 my-2 text-text-muted" {...props}>
                  {children}
                </blockquote>
              ),
              hr: () => <hr className="my-4 border-border" />,
              strong: ({ children, ...props }) => (
                <strong className="font-semibold" {...props}>
                  {children}
                </strong>
              ),
            }}
          >
            {content}
          </ReactMarkdown>
          </div>
        </div>
      </div>
    </div>
  );
}
