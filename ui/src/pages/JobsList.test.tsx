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

describe("JobsList filters", () => {
  const NOW = Math.floor(Date.now() / 1000);
  const JOBS = [
    { name: "monitoring/fresh", description: "刚建的监控", category: "monitoring",
      enabled: true, keywords: ["ai"], output_template: "o.md", is_running: false,
      file_mtime: NOW - 3600, state: null },
    { name: "research/old", description: "老研究", category: "research",
      enabled: false, keywords: ["rag"], output_template: "o.md", is_running: false,
      file_mtime: NOW - 60 * 86400, state: { last_run_at: "2026-09-20T06:00:00+0800" } },
    { name: "research/busy", description: "在跑", category: "research",
      enabled: true, keywords: [], output_template: "o.md", is_running: true,
      file_mtime: NOW - 86400, state: null },
  ];

  function mockJobs(list = JOBS) {
    const fn = vi.fn(async (url: string) => {
      const path = String(url);
      if (path.includes("/api/jobs/categories")) {
        return jsonResponse({ categories: [
          { name: "monitoring", count: 1 }, { name: "research", count: 2 }] });
      }
      if (path.includes("/api/jobs/templates")) return jsonResponse({ templates: [] });
      if (path.includes("/api/jobs")) return jsonResponse(list);
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fn);
    return fn;
  }

  async function renderList() {
    render(<MemoryRouter><JobsListPage /></MemoryRouter>);
    await screen.findByText("monitoring/fresh");
  }

  it("filters by status", async () => {
    mockJobs();
    await renderList();

    fireEvent.click(screen.getByText("停用"));
    await waitFor(() => expect(screen.queryByText("monitoring/fresh")).toBeNull());
    expect(screen.getByText("research/old")).toBeTruthy();

    fireEvent.click(screen.getByText("运行中"));
    await waitFor(() => expect(screen.queryByText("research/old")).toBeNull());
    expect(screen.getByText("research/busy")).toBeTruthy();
  });

  it("filters by when the file was last modified", async () => {
    mockJobs();
    await renderList();

    fireEvent.change(screen.getByLabelText("按最后修改时间筛选"), { target: { value: "7d" } });
    await waitFor(() => expect(screen.queryByText("research/old")).toBeNull());
    expect(screen.getByText("monitoring/fresh")).toBeTruthy();
    expect(screen.getByText("research/busy")).toBeTruthy();
  });

  it("sorts by name, most recently run and never run", async () => {
    mockJobs();
    await renderList();

    fireEvent.change(screen.getByLabelText("排序方式"), { target: { value: "ran" } });
    await waitFor(() => {
      const names = screen.getAllByText(/monitoring\/fresh|research\/old|research\/busy/);
      expect(names.length).toBeGreaterThan(0);
    });

    fireEvent.change(screen.getByLabelText("排序方式"), { target: { value: "never" } });
    await waitFor(() => expect(screen.getByText("从未运行优先")).toBeTruthy());
  });

  it("searches description and keywords, not just the name", async () => {
    mockJobs();
    await renderList();

    fireEvent.change(screen.getByPlaceholderText("搜索..."), { target: { value: "rag" } });
    await waitFor(() => expect(screen.queryByText("monitoring/fresh")).toBeNull());
    expect(screen.getByText("research/old")).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText("搜索..."), { target: { value: "刚建的" } });
    await waitFor(() => expect(screen.getByText("monitoring/fresh")).toBeTruthy());
  });

  it("shows how many jobs pass the filters", async () => {
    mockJobs();
    await renderList();
    expect(screen.getByText("3 / 3")).toBeTruthy();

    fireEvent.click(screen.getByText("停用"));
    await waitFor(() => expect(screen.getByText("1 / 3")).toBeTruthy());
  });

  it("clears every filter in one click", async () => {
    mockJobs();
    await renderList();

    fireEvent.click(screen.getByText("停用"));
    fireEvent.change(screen.getByPlaceholderText("搜索..."), { target: { value: "nothing" } });
    await waitFor(() => expect(screen.getByText("0 / 3")).toBeTruthy());

    fireEvent.click(screen.getByText("清除筛选"));
    await waitFor(() => expect(screen.getByText("3 / 3")).toBeTruthy());
  });
});
