import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AdminOnly } from "./App";
import { useAuthStore } from "@/lib/auth-store";

afterEach(cleanup);

describe("AdminOnly route guard", () => {
  it("blocks a viewer deep-linking to an admin page", () => {
    useAuthStore.setState({ user: { username: "v", role: "viewer" } as never });
    render(
      <MemoryRouter>
        <AdminOnly><div>调度器内容</div></AdminOnly>
      </MemoryRouter>,
    );
    expect(screen.getByText(/仅限管理员访问/)).toBeTruthy();
    expect(screen.getByText(/viewer/)).toBeTruthy();
    expect(screen.queryByText("调度器内容")).toBeNull();
  });

  it("lets admins through", () => {
    useAuthStore.setState({ user: { username: "a", role: "admin" } as never });
    render(
      <MemoryRouter>
        <AdminOnly><div>调度器内容</div></AdminOnly>
      </MemoryRouter>,
    );
    expect(screen.getByText("调度器内容")).toBeTruthy();
    expect(screen.queryByText(/仅限管理员访问/)).toBeNull();
  });
});
