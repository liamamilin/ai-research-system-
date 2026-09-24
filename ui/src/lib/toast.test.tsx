import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, act, fireEvent, cleanup } from "@testing-library/react";
import { useEffect } from "react";
import { ToastProvider, useToast, useToasts, errorMessage } from "./toast";

function Probe({ effectValue = true }: { effectValue?: boolean }) {
  const toast = useToast();
  const toasts = useToasts();
  const seen = (globalThis as any).__toastRefs ?? [];
  seen.push(toast);
  (globalThis as any).__toastRefs = seen;
  useEffect(() => {
    if (effectValue) toast.error("加载失败：服务器 500", "GET /api/reports");
  }, [toast, effectValue]);
  return <div>count:{toasts.length}</div>;
}

afterEach(() => {
  cleanup();
  delete (globalThis as any).__toastRefs;
  vi.useRealTimers();
});

describe("toast", () => {
  it("shows an error toast and keeps it until dismissed", () => {
    render(
      <ToastProvider>
        <Probe />
      </ToastProvider>,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("加载失败：服务器 500");
    expect(screen.getByText("GET /api/reports")).toBeTruthy();

    fireEvent.click(screen.getByLabelText("关闭通知"));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("auto-dismisses success toasts but not errors", () => {
    vi.useFakeTimers();
    function Mixed() {
      const toast = useToast();
      return (
        <>
          <button onClick={() => toast.success("已保存")}>ok</button>
          <button onClick={() => toast.error("保存失败")}>bad</button>
        </>
      );
    }
    render(
      <ToastProvider>
        <Mixed />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByText("ok"));
    fireEvent.click(screen.getByText("bad"));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getAllByRole("alert")).toHaveLength(1);

    act(() => { vi.advanceTimersByTime(3000); });
    expect(screen.queryByText("已保存")).toBeNull();
    expect(screen.getByText("保存失败")).toBeTruthy();
  });

  // Regression: the toast API must keep the same reference across renders,
  // otherwise effects depending on it re-run on every notification.
  it("keeps the toast API referentially stable", () => {
    const refs: unknown[] = [];
    function Track() {
      const toast = useToast();
      const toasts = useToasts();
      refs.push(toast);
      return <button onClick={() => toast.info("x")}>toasts:{toasts.length}</button>;
    }
    render(
      <ToastProvider>
        <Track />
      </ToastProvider>,
    );
    const first = refs[0];
    fireEvent.click(screen.getByText(/toasts:/));
    expect(screen.getByText(/toasts:1/)).toBeTruthy();
    expect(refs.length).toBeGreaterThan(1);
    expect(refs.every((r) => r === first)).toBe(true);
  });

  it("does not loop when an effect depends on the toast API", () => {
    const effect = vi.fn();
    function Looping() {
      const toast = useToast();
      useEffect(() => { effect(toast); }, [toast]);
      return null;
    }
    render(
      <ToastProvider>
        <Looping />
      </ToastProvider>,
    );
    expect(effect).toHaveBeenCalledTimes(1);
  });

  it("falls back gracefully outside a provider", () => {
    function Bare() {
      const toast = useToast();
      expect(() => toast.error("x")).not.toThrow();
      return <div>bare</div>;
    }
    render(<Bare />);
    expect(screen.getByText("bare")).toBeTruthy();
  });
});

describe("errorMessage", () => {
  it("uses the error message, string, or fallback", () => {
    expect(errorMessage(new Error("网络不可达"))).toBe("网络不可达");
    expect(errorMessage("plain")).toBe("plain");
    expect(errorMessage(null, "默认")).toBe("默认");
    expect(errorMessage(new Error(""))).toBe("操作失败");
  });
});
