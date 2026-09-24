import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ReportsPage } from "./Reports";

const TREE = [
  {
    type: "dir",
    name: "research",
    children: [
      { type: "file", path: "research/a.md", title: "A" },
      { type: "file", path: "research/b.md", title: "B" },
    ],
  },
  { type: "file", path: "root.md", title: "Root" },
];

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
    if (path.includes("/api/reports/tree")) return jsonResponse(TREE);
    if (path.includes("/api/reports/categories")) return jsonResponse(["cat"]);
    if (path.includes("/api/reports/tags")) return jsonResponse({ tags: [] });
    if (path.includes("/api/reports/search")) return jsonResponse({ results: [], total: 0 });
    return jsonResponse({ items: [], total: 0, page: 1, pages: 1 });
  });
  vi.stubGlobal("fetch", fn);
}

beforeEach(() => {
  window.sessionStorage.clear();
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Reports tree state", () => {
  it("remembers the last visited report and highlights it after returning", async () => {
    const { unmount } = render(
      <MemoryRouter><ReportsPage /></MemoryRouter>,
    );

    await screen.findByTitle("research/a.md");
    fireEvent.click(screen.getByTitle("research/a.md"));

    const saved = JSON.parse(
      window.sessionStorage.getItem("ai-research-console:reports-ui") || "{}",
    );
    expect(saved.openDirs).toContain("research");
    expect(saved.lastVisited).toBe("research/a.md");

    unmount();
    render(<MemoryRouter><ReportsPage /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.getByTitle("research/a.md").className).toContain("bg-accent/20");
    });
  });

  it("remembers an explicitly collapsed directory", async () => {
    const { unmount } = render(<MemoryRouter><ReportsPage /></MemoryRouter>);
    await screen.findByTitle("research/a.md");

    fireEvent.click(screen.getByText("research"));
    expect(screen.queryByTitle("research/a.md")).toBeNull();

    unmount();
    render(<MemoryRouter><ReportsPage /></MemoryRouter>);
    await screen.findByText("research");
    expect(screen.queryByTitle("research/a.md")).toBeNull();
  });

  it("restores the collapsed state of the tree panel", async () => {
    const { unmount } = render(<MemoryRouter><ReportsPage /></MemoryRouter>);
    await screen.findByText("research");
    fireEvent.click(screen.getByText("目录"));
    expect(screen.queryByText("research")).toBeNull();

    unmount();
    render(<MemoryRouter><ReportsPage /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.queryByText("research")).toBeNull();
    });
  });
});

describe("Reports filters", () => {
  const ITEM = { path: "research/a.md", title: "A", category: "research", favorite: false };

  function mockWith(extra: { total?: number; tree?: any[] } = {}) {
    const fn = vi.fn(async (url: string) => {
      const path = String(url);
      if (path.includes("/api/reports/tree")) {
        return jsonResponse(extra.tree ?? [
          { name: "research", type: "dir", count: extra.total ?? 1,
            children: [{ name: "a.md", type: "file", path: ITEM.path, title: "A" }] },
        ]);
      }
      if (path.includes("/api/reports/categories")) return jsonResponse(["research"]);
      if (path.includes("/api/reports/tags")) return jsonResponse({ tags: [] });
      if (path.includes("/api/reports")) {
        return jsonResponse({ items: [ITEM], total: extra.total ?? 1, page: 1, pages: 1 });
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  async function renderReports() {
    render(
      <MemoryRouter initialEntries={["/reports"]}>
        <Routes><Route path="/reports" element={<ReportsPage />} /></Routes>
      </MemoryRouter>,
    );
    await screen.findByText("A");
  }

  it("sends the chosen time range to both the list and the tree", async () => {
    const fn = mockWith();
    await renderReports();

    fireEvent.change(screen.getByLabelText("按时间筛选"), { target: { value: "7d" } });
    await waitFor(() => {
      const urls = fn.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("since="))).toBe(true);
      expect(urls.some((u) => u.includes("/api/reports/tree") && u.includes("since="))).toBe(true);
    });
  });

  it("filters by the latest round", async () => {
    const fn = mockWith();
    await renderReports();

    fireEvent.click(screen.getByText("最新一轮"));
    await waitFor(() => {
      const urls = fn.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("latest_round=true"))).toBe(true);
      expect(urls.some((u) => u.includes("/api/reports/tree") && u.includes("latest_round=true"))).toBe(true);
    });
  });

  it("passes the sort order through", async () => {
    const fn = mockWith();
    await renderReports();

    fireEvent.change(screen.getByLabelText("排序方式"), { target: { value: "coverage" } });
    await waitFor(() => {
      const urls = fn.mock.calls.map((c) => String(c[0]));
      expect(urls.some((u) => u.includes("sort=coverage"))).toBe(true);
    });
  });

  it("shows date inputs for a custom range", async () => {
    mockWith();
    await renderReports();
    expect(screen.queryByLabelText("起始日期")).toBeNull();

    fireEvent.change(screen.getByLabelText("按时间筛选"), { target: { value: "custom" } });
    const from = await screen.findByLabelText("起始日期");
    fireEvent.change(from, { target: { value: "2026-09-01" } });
    await waitFor(() => {
      expect(screen.getByLabelText("结束日期")).toBeTruthy();
    });
    expect(screen.getByText("清除筛选")).toBeTruthy();
  });

  it("shows the count the tree reports for each directory", async () => {
    mockWith({ total: 24 });
    await renderReports();
    expect(await screen.findByText("24")).toBeTruthy();
  });
});
