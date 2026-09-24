import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Database, Loader2, Search, Sparkles } from "lucide-react";

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
  const [pending, setPending] = useState("");
  const requestId = useRef(0);

  const handleAsk = async (value?: string) => {
    const q = (value ?? question).trim();
    if (q.length < 2) {
      setError("请输入至少 2 个字符的问题");
      return;
    }
    // Supersede any in-flight request: a slower earlier answer must not
    // overwrite the answer to the question the user actually asked last.
    const id = ++requestId.current;
    setBusy(true);
    setError("");
    setNotice("");
    setPending(q);
    setResult(null);
    try {
      const answer = await askQuestion(q);
      if (id !== requestId.current) return;
      setResult(answer);
    } catch (err: unknown) {
      if (id !== requestId.current) return;
      setError(err instanceof Error ? err.message : "提问失败");
    } finally {
      if (id === requestId.current) {
        setBusy(false);
        setPending("");
      }
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

      {busy && (
        <div className="card p-4 flex items-center gap-3 text-sm" role="status" aria-live="polite">
          <Loader2 className="w-4 h-4 animate-spin text-accent shrink-0" />
          <span className="text-text-muted">正在检索报告并生成回答…</span>
          <span className="text-xs text-text-muted/70 truncate ml-auto max-w-[50%]">{pending}</span>
        </div>
      )}

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

          {result.citations.length === 0 && (
            <div role="alert"
              className="rounded-md border border-warning/50 bg-amber-900/10 px-3 py-2 text-xs text-warning">
              本次检索没有命中任何报告，下面这段回答没有报告作为依据，仅供参考。
            </div>
          )}

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
