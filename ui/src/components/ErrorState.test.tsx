import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ErrorState, RoleGate } from "./ErrorState";
import { useAuthStore } from "@/lib/auth-store";

beforeEach(() => {
  useAuthStore.setState({ user: { username: "u", role: "viewer" } as never });
});

afterEach(cleanup);

describe("ErrorState", () => {
  it("shows the empty state when there is no error", () => {
    render(<ErrorState empty="暂无报告" />);
    expect(screen.getByText("暂无报告")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("distinguishes a failure from empty data and offers a retry", () => {
    const onRetry = vi.fn();
    render(<ErrorState error="HTTP 500" onRetry={onRetry} empty="暂无报告" />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("加载失败：HTTP 500");
    expect(screen.queryByText("暂无报告")).toBeNull();
    fireEvent.click(screen.getByText("重试"));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("omits the retry button when no handler is given", () => {
    render(<ErrorState error="HTTP 403" />);
    expect(screen.queryByText("重试")).toBeNull();
  });
});

describe("RoleGate", () => {
  it("hides editor-only actions from viewers", () => {
    render(
      <RoleGate roles={["editor", "admin"]}>
        <button>加星标</button>
      </RoleGate>,
    );
    expect(screen.queryByText("加星标")).toBeNull();
  });

  it("shows editor-only actions to editors and admins", () => {
    useAuthStore.setState({ user: { username: "u", role: "editor" } as never });
    render(
      <RoleGate roles={["editor", "admin"]}>
        <button>加星标</button>
      </RoleGate>,
    );
    expect(screen.getByText("加星标")).toBeTruthy();

    cleanup();
    useAuthStore.setState({ user: { username: "u", role: "admin" } as never });
    render(
      <RoleGate roles={["admin"]}>
        <button>删除用户</button>
      </RoleGate>,
    );
    expect(screen.getByText("删除用户")).toBeTruthy();
  });

  it("renders the fallback instead of children when denied", () => {
    render(
      <RoleGate roles={["admin"]} fallback={<span>无权限</span>}>
        <button>删除用户</button>
      </RoleGate>,
    );
    expect(screen.getByText("无权限")).toBeTruthy();
    expect(screen.queryByText("删除用户")).toBeNull();
  });
});
