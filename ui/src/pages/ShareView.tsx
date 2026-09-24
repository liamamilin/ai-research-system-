import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { SharedReport } from "@/api";

export function ShareViewPage() {
  const { token = "" } = useParams();
  const [data, setData] = useState<SharedReport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setError("");
    fetch(`/api/share/${encodeURIComponent(token)}`)
      .then(async (resp) => {
        const body = await resp.json().catch(() => null);
        if (!resp.ok) {
          throw new Error(body?.error?.message || "分享链接无效或已过期");
        }
        return body as SharedReport;
      })
      .then((shared) => {
        if (alive) setData(shared);
      })
      .catch((err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "加载失败");
      });
    return () => {
      alive = false;
    };
  }, [token]);

  return (
    <div className="min-h-screen bg-bg text-text">
      <div className="max-w-4xl mx-auto px-4 py-8 space-y-4">
        <div className="flex items-center gap-2 text-sm text-text-muted">
          <span className="font-semibold text-text">AI Research Console</span>
          <span>· 分享报告（只读）</span>
        </div>

        {error && <div className="card p-4 text-danger text-sm">{error}</div>}

        {data && (
          <>
            <div>
              <h1 className="text-lg font-semibold">{data.title}</h1>
              <div className="text-xs text-text-muted mt-1">
                {data.path}
                {data.shared_by && ` · 由 ${data.shared_by} 分享`}
                {data.expires_at &&
                  ` · ${new Date(data.expires_at * 1000).toLocaleDateString()} 到期`}
              </div>
            </div>
            <div className="card p-4 md:p-6 overflow-x-auto">
              <div className="md-body md-body-wide">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.content}</ReactMarkdown>
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
