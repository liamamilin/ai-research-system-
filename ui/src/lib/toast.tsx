import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastKind = "info" | "success" | "warning" | "error";

export interface Toast {
  id: number;
  kind: ToastKind;
  message: string;
  detail?: string;
}

export interface ToastApi {
  push: (kind: ToastKind, message: string, detail?: string) => void;
  info: (message: string, detail?: string) => void;
  success: (message: string, detail?: string) => void;
  warning: (message: string, detail?: string) => void;
  error: (message: string, detail?: string) => void;
  dismiss: (id: number) => void;
  clear: () => void;
}

// Two contexts on purpose: the API context is referentially stable, so adding
// a toast never invalidates a useEffect dependency; the state context changes.
const ToastApiContext = createContext<ToastApi | null>(null);
const ToastStateContext = createContext<Toast[]>([]);

const DEFAULT_TTL_MS: Record<ToastKind, number> = {
  info: 4000,
  success: 3000,
  warning: 7000,
  error: 0, // errors stay until dismissed
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
    const timer = timers.current[id];
    if (timer) {
      clearTimeout(timer);
      delete timers.current[id];
    }
  }, []);

  const push = useCallback((kind: ToastKind, message: string, detail?: string) => {
    const id = nextId.current++;
    setToasts((prev) => [...prev.slice(-4), { id, kind, message, detail }]);
    const ttl = DEFAULT_TTL_MS[kind];
    if (ttl > 0) {
      timers.current[id] = setTimeout(() => dismiss(id), ttl);
    }
  }, [dismiss]);

  useEffect(() => {
    const pending = timers.current;
    return () => {
      Object.values(pending).forEach(clearTimeout);
    };
  }, []);

  const clear = useCallback(() => setToasts([]), []);

  // The API is memoized without `toasts` so consumers can safely use it in
  // effect dependency arrays — a new toast must not retrigger effects.
  const api = useMemo<ToastApi>(() => ({
    push,
    dismiss,
    clear,
    info: (m, d) => push("info", m, d),
    success: (m, d) => push("success", m, d),
    warning: (m, d) => push("warning", m, d),
    error: (m, d) => push("error", m, d),
  }), [push, dismiss, clear]);

  return (
    <ToastApiContext.Provider value={api}>
      <ToastStateContext.Provider value={toasts}>
        {children}
        <ToastViewport toasts={toasts} onDismiss={dismiss} />
      </ToastStateContext.Provider>
    </ToastApiContext.Provider>
  );
}

function ToastViewport({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm" role="region" aria-label="通知">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role={toast.kind === "error" ? "alert" : "status"}
          className={cn(
            "card px-3 py-2.5 flex items-start gap-2 shadow-lg text-sm",
            toast.kind === "error" && "border-danger/70",
            toast.kind === "warning" && "border-warning/70"
          )}
        >
          <span className="mt-0.5 shrink-0">
            {toast.kind === "error" ? <XCircle className="w-4 h-4 text-danger" />
              : toast.kind === "warning" ? <AlertTriangle className="w-4 h-4 text-warning" />
                : toast.kind === "success" ? <CheckCircle2 className="w-4 h-4 text-success" />
                  : <Info className="w-4 h-4 text-accent" />}
          </span>
          <div className="flex-1 min-w-0">
            <div className="break-words">{toast.message}</div>
            {toast.detail && (
              <div className="text-xs text-text-muted mt-0.5 break-words">{toast.detail}</div>
            )}
          </div>
          <button
            onClick={() => onDismiss(toast.id)}
            className="text-text-muted hover:text-foreground shrink-0"
            aria-label="关闭通知"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ))}
    </div>
  );
}

const NOOP_TOAST_API: ToastApi = {
  push: () => {},
  info: () => {},
  success: () => {},
  warning: () => {},
  error: () => {},
  dismiss: () => {},
  clear: () => {},
};

/** Stable notification API. Safe to use in effect dependency arrays. */
export function useToast(): ToastApi {
  // Outside a provider: no-op so components stay usable in isolation/tests.
  return useContext(ToastApiContext) ?? NOOP_TOAST_API;
}

/** Current visible notifications (re-renders on every change). */
export function useToasts(): Toast[] {
  return useContext(ToastStateContext);
}

/** Message for a caught error, used so every surface reports failures the same way. */
export function errorMessage(err: unknown, fallback = "操作失败"): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === "string" && err) return err;
  return fallback;
}
