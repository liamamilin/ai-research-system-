import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobsListPage } from "./JobsList";
import { useAuthStore } from "@/lib/auth-store";

const JOBS = [
  { name: "research/a", description: "", category: "research", state: null, is_running: false },
  { name: "monitoring/b", description: "", category: "monitoring", state: null, is_running: false },
];

const CATEGORIES = {
  categories: [
    { name: "research", count: 1 },
    { name: "monitoring", count: 1 },
  ],
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch() {
  const fn = vi.fn(async (url: string) => {
    const path = String(url);
    if (path.includes("/api/jobs/categories")) return jsonResponse(CATEGORIES);
    if (path.includes("/api/jobs/templates")) {
      return jsonResponse({
        templates: [
          {
            key: "_daily",
            name: "daily",
            filename: "_daily.yaml",
            label: "每日情报速报",
            category: "monitoring",
            description: "24h 速报",
            builtin: true,
            variables: ["name", "keywords", "date"],
            output_template: "output/monitoring/{date}_{name}.md",
            updated_at: "2026-09-24 08:00:00",
            prompt: "简报 {name}，关键词 {keywords}，日期 {date}。请给出证据与结论。",
            keywords: ["ai", "agent"],
            language: "zh",
            timeout_seconds: 1800,
            schedule: { type: "daily", time: "08:00" },
            content: "name: x",
          },
        ],
      });
    }
    if (path.includes("/api/jobs")) return jsonResponse(JOBS);
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fn);
}

beforeEach(() => {
  useAuthStore.setState({
    user: {
      id: 1,
      username: "admin",
      role: "admin",
      disabled: false,
      created_at: "2026-01-01T00:00:00",
      last_login_at: null,
    },
  });
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Jobs category picker", () => {
  it("shows category filter pills with counts", async () => {
    render(<MemoryRouter><JobsListPage /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText(/全部分类/)).toBeTruthy();
    });
    const researchPill = screen.getByTitle("research：1 个 job");
    expect(researchPill.textContent).toContain("1");
  });

  it("filters the list by category", async () => {
    render(<MemoryRouter><JobsListPage /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByText("research/a")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("monitoring：1 个 job"));
    expect(screen.queryByText("research/a")).toBeNull();
    expect(screen.getByText("monitoring/b")).toBeTruthy();
  });

  it("normalizes typed category and warns for new ones", async () => {
    render(<MemoryRouter><JobsListPage /></MemoryRouter>);
    fireEvent.click(screen.getByText(/\+ 新建/));
    await waitFor(() => {
      expect(screen.getByPlaceholderText("选择已有分类或输入新分类")).toBeTruthy();
    });

    const input = screen.getByPlaceholderText("选择已有分类或输入新分类") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  Research  " } });
    expect(input.value).toBe("research");
    expect(screen.queryByText(/将新建分类/)).toBeNull();

    fireEvent.change(input, { target: { value: "reserch" } });
    expect(screen.getByText(/将新建分类「reserch」/)).toBeTruthy();
  });

  it("fills the category when clicking an existing chip", async () => {
    render(<MemoryRouter><JobsListPage /></MemoryRouter>);
    fireEvent.click(screen.getByText(/\+ 新建/));
    await waitFor(() => {
      expect(screen.getByTitle("使用分类 research")).toBeTruthy();
    });
    fireEvent.click(screen.getByTitle("使用分类 research"));
    const input = screen.getByPlaceholderText("选择已有分类或输入新分类") as HTMLInputElement;
    expect(input.value).toBe("research");
  });
});
