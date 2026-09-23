import { useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Database, Search, Sparkles } from "lucide-react";

import { askQuestion, reindexVectors, type QaAnswer } from "@/api";
import { useAuthStore } from "@/lib/auth-store";
import { cn } from "@/lib/utils";

const EXAMPLES = [
  "最近一轮有哪些值得立刻执行的行动？",
  "模型定价有什么变化，影响哪些工作流？",
  "有没有关于 agent 记忆的新进展？",
];

export function AskPage() {
  const user = useAuthStore((s) => s.user);
  const canEdit = user?.role === "editor" || user?.role === "admin";

  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<QaAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const handleAsk = async (value?: string) => {
    const q = (value ?? question).trim();
    if (q.length < 2) {
      setError("请输入至少 2 个字符的问题");
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    setResult(null);
    try {
      setResult(await askQuestion(q));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "提问失败");
    } finally {
      setBusy(false);
    }
  };

  const handleReindex = async () => {
    setReindexing(true);
    setError("");
    try {
      const stats = await reindexVectors();
      setNotice(
        `索引完成：新嵌入 ${stats.embedded} 篇 · 跳过 ${stats.skipped} 篇 · 失败 ${stats.failed} 篇` +
        `（共 ${stats.stats.documents} 篇 / ${stats.stats.chunks} 块）`,
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "重建索引失败");
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center gap-3">
        <h1 className="text-lg font-semibold flex-1">问答</h1>
        {result && (
          <span className={cn("badge border text-xs",
            result.mode === "hybrid"
              ? "border-success/60 text-success"
              : "border-border text-text-muted")}>
            {result.mode === "hybrid" ? "语义+关键词" : "关键词"}
          </span>
        )}
        {canEdit && (
          <button onClick={handleReindex} disabled={reindexing} className="btn text-xs"
                  title="为报告建立/更新向量索引（需配置 ai.embedding_model）">
            <Database className="w-3 h-3" />
            {reindexing ? "索引中..." : "重建索引"}
          </button>
        )}
      </div>

      <div className="card p-4 space-y-3">
        <div className="flex gap-2">
          <input
            className="input flex-1"
            placeholder="跨全部报告提问，例如：最近的模型价格变化？"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAsk()}
          />
          <button onClick={() => handleAsk()} disabled={busy} className="btn btn-primary">
            <Search className="w-3.5 h-3.5" />
            {busy ? "检索中..." : "提问"}
          </button>
        </div>
        {!result && (
          <div className="flex flex-wrap gap-1.5">
            {EXAMPLES.map((example) => (
              <button key={example}
                      onClick={() => { setQuestion(example); handleAsk(example); }}
                      className="text-xs text-text-muted border border-border rounded-full px-2.5 py-1 hover:bg-bg-hover hover:text-text transition">
                {example}
              </button>
            ))}
          </div>
        )}
      </div>

      {notice && (
        <div className="text-xs text-text-muted bg-bg-card border border-border rounded-md px-3 py-2">
          {notice}
        </div>
      )}
      {error && (
        <div className="text-sm text-danger bg-red-900/20 px-3 py-2 rounded-md">{error}</div>
      )}

      {result && (
        <>
          <div className="card p-4 md:p-6">
            <div className="flex items-center gap-2 text-xs text-text-muted mb-3">
              <Sparkles className="w-3.5 h-3.5" />
              回答
              {result.usage?.total_tokens ? (
                <span>· {result.usage.total_tokens} tokens</span>
              ) : null}
            </div>
            <div className="prose prose-invert max-w-none text-sm leading-relaxed">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.answer}</ReactMarkdown>
            </div>
          </div>

          {result.citations.length > 0 && (
            <div className="card p-4 space-y-2">
              <div className="text-xs text-text-muted uppercase">引用（{result.citations.length}）</div>
              {result.citations.map((c) => (
                <div key={`${c.index}-${c.path}`} className="text-xs border-b border-border/50 last:border-0 pb-2 last:pb-0">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-text-muted">[{c.index}]</span>
                    <Link
                      to={`/reports/view?path=${encodeURIComponent(c.path)}`}
                      className="text-accent hover:underline truncate"
                    >
                      {c.title || c.path}
                    </Link>
                    <span className="text-text-muted/70">{c.source}</span>
                  </div>
                  {c.snippet && (
                    <div className="text-text-muted mt-0.5 line-clamp-2 pl-6">{c.snippet}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
