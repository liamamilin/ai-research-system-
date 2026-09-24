import type { ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthStore } from "@/lib/auth-store";

interface Props {
  /** The failure that prevented loading. When null, the empty state is shown. */
  error?: string | null;
  empty?: string;
  onRetry?: () => void;
  className?: string;
  compact?: boolean;
}

/**
 * Renders a load failure or an empty state — never both.
 *
 * Pages used to catch errors and render "暂无数据", so a 500 looked exactly
 * like an empty database.
 */
export function ErrorState({ error, empty = "暂无数据", onRetry, className, compact }: Props) {
  if (!error) {
    return (
      <div className={cn("text-text-muted text-center", compact ? "py-6 text-xs" : "py-10 text-sm", className)}>
        {empty}
      </div>
    );
  }
  return (
    <div
      role="alert"
      className={cn(
        "rounded-md border border-danger/50 bg-red-900/10 text-center space-y-2",
        compact ? "py-3 px-3" : "py-6 px-4",
        className
      )}
    >
      <div className="flex items-center justify-center gap-2 text-danger text-sm">
        <AlertTriangle className="w-4 h-4 shrink-0" />
        <span>加载失败：{error}</span>
      </div>
      {onRetry && (
        <button onClick={onRetry} className="btn text-xs">
          <RefreshCw className="w-3.5 h-3.5 mr-1" />
          重试
        </button>
      )}
    </div>
  );
}

interface RoleGateProps {
  roles: Array<"viewer" | "editor" | "admin">;
  children: ReactNode;
  fallback?: ReactNode;
}

/** Renders children only for the listed roles (used for editor/admin-only UI). */
export function RoleGate({ roles, children, fallback = null }: RoleGateProps) {
  const role = useAuthStore((s) => s.user?.role);
  if (!role || !roles.includes(role)) return <>{fallback}</>;
  return <>{children}</>;
}
