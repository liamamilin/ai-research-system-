import { useCallback, useEffect, useState } from "react";
import { Link2, ShieldOff } from "lucide-react";

import { listShareLinks, revokeShareLink, type ShareLink } from "@/api";
import { cn } from "@/lib/utils";

const STATE_STYLE: Record<ShareLink["state"], string> = {
  live: "text-success",
  expired: "text-text-muted",
  revoked: "text-danger",
};

const STATE_LABEL: Record<ShareLink["state"], string> = {
  live: "生效中",
  expired: "已过期",
  revoked: "已撤销",
};

function when(epoch?: number | null): string {
  if (!epoch) return "-";
  return new Date(epoch * 1000).toLocaleDateString();
}

/** Every read-only link this console has handed out, and a way to withdraw one.
 *
 * A share token is self-contained, so before this existed the only way to make
 * a link stop working was to rotate the app secret -- which signs you out and
 * invalidates every other link at the same time.
 */
export function ShareLinks() {
  const [links, setLinks] = useState<ShareLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await listShareLinks();
      setLinks(data.links || []);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const revoke = async (jti: string) => {
    if (!window.confirm("撤销这条分享链接？对方将立即无法访问，报告本身不受影响。")) return;
    setBusy(jti);
    setError("");
    try {
      await revokeShareLink(jti);
      await load();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "撤销失败");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="card p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Link2 className="w-4 h-4 text-text-muted" />
        <h2 className="text-sm font-medium">分享链接</h2>
        <span className="text-xs text-text-muted">
          {loading ? "加载中…" : `${links.filter((l) => l.state === "live").length} 条生效中`}
        </span>
      </div>

      {error && <p className="text-xs text-danger">{error}</p>}

      {!loading && !links.length && !error && (
        <p className="text-xs text-text-muted">
          还没有分享链接。在报告页点「分享」可生成只读链接。
        </p>
      )}

      <div className="space-y-2">
        {links.map((link) => (
          <div
            key={link.jti}
            className="flex flex-wrap items-center gap-2 text-xs border-t border-border pt-2 first:border-0 first:pt-0"
          >
            <span className="font-mono break-all min-w-0 flex-1" title={link.path}>
              {link.path}
            </span>
            <span className="text-text-muted shrink-0">
              {link.by} · {when(link.created_at)} → {when(link.expires_at)}
            </span>
            <span className={cn("shrink-0", STATE_STYLE[link.state])}>
              {STATE_LABEL[link.state]}
            </span>
            {link.state === "live" && (
              <button
                className="btn text-xs shrink-0"
                disabled={!!busy}
                onClick={() => revoke(link.jti)}
                aria-label={`撤销 ${link.path}`}
              >
                {busy === link.jti ? "撤销中…" : "撤销"}
                <ShieldOff className="w-3 h-3" />
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
