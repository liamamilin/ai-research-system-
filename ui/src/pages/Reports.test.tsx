import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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
