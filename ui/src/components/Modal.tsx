import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  className?: string;
  /** Unsaved work: closing asks for confirmation first. */
  dirty?: boolean;
  /** Hide the header close button and ignore Escape / backdrop clicks. */
  dismissible?: boolean;
  onConfirmClose?: () => void;
  confirmLabel?: string;
}

const FOCUSABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
].join(",");

/**
 * Accessible modal: Escape to close, focus trapped inside, focus restored on
 * close, body scroll locked, and a confirmation step when there is unsaved work.
 */
export function Modal({
  open, onClose, title, children, className,
  dirty = false, dismissible = true, onConfirmClose, confirmLabel = "放弃修改",
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    if (!open) {
      setConfirming(false);
      return;
    }
    restoreRef.current = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    return () => {
      document.body.style.overflow = previousOverflow;
      restoreRef.current?.focus?.();
    };
  }, [open]);

  const requestClose = useCallback(() => {
    if (!dismissible) return;
    if (dirty && !confirming) {
      setConfirming(true);
      return;
    }
    setConfirming(false);
    onClose();
  }, [dismissible, dirty, confirming, onClose]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      requestClose();
      return;
    }
    if (event.key !== "Tab") return;
    const panel = panelRef.current;
    if (!panel) return;
    const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE))
      .filter((el) => !el.hasAttribute("hidden") && el.getAttribute("aria-hidden") !== "true");
    if (nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (event.shiftKey && (active === first || !panel.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={requestClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        className={cn("card flex flex-col max-h-[88vh]", className || "w-full max-w-2xl")}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        {confirming ? (
          <div className="p-5 space-y-4">
            <div className="flex items-center gap-2 text-sm text-warning">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>有未保存的修改，确定要关闭吗？</span>
            </div>
            <div className="flex justify-end gap-2">
              <button className="btn text-xs" onClick={() => setConfirming(false)}>
                继续编辑
              </button>
              <button
                className="btn btn-danger text-xs"
                onClick={() => {
                  setConfirming(false);
                  if (onConfirmClose) onConfirmClose();
                  else onClose();
                }}
              >
                {confirmLabel}
              </button>
            </div>
          </div>
        ) : (
          children
        )}
      </div>
    </div>
  );
}

export function ModalHeader({ title, onClose, dismissible = true }: {
  title: ReactNode;
  onClose: () => void;
  dismissible?: boolean;
}) {
  return (
    <div className="px-4 py-3 border-b border-border flex items-center gap-2">
      <span className="font-medium text-sm">{title}</span>
      {dismissible && (
        <button
          type="button"
          onClick={onClose}
          className="ml-auto text-text-muted hover:text-foreground"
          aria-label="关闭"
        >
          <X className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
