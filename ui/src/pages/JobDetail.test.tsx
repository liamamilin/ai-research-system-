import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobDetailPage } from "./JobDetail";

const JOB = {
  name: "research/demo",
  description: "demo",
  category: "research",
  enabled: true,
  keywords: ["a"],
  output_template: "output/{date}_{name}.md",
  state: {
    job_name: "research/demo",
    last_run_at: "2026-09-24T02:39:43+0800",
    last_status: "success",
    last_output: "output/research/2026-09-24_demo.md",
    last_error: null,
    last_duration_seconds: 191.3,
    last_usage: { total_tokens: 100, model: "m", searches: 3 },
  },
  is_running: false,
  yaml_content: 'name: "demo"\nprompt: "x"\n',
  yaml_mtime: 1,
  file_path: "jobs/research/demo.yaml",
  prompt: "x",
  language: "zh",
  timeout_seconds: 600,
  schedule: null,
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

const PERSISTED = {
  runs: [
    {
      run_id: "run-1",
      status: "success",
      started_at: "2026-09-24T02:36:00+0800",
      finished_at: "2026-09-24T02:39:10+0800",
      events: 3,
    },
  ],
  run: {
    run_id: "run-1",
    status: "success",
    started_at: "2026-09-24T02:36:00+0800",
    finished_at: "2026-09-24T02:39:10+0800",
    events: 3,
  },
  events: [
    { run_id: "run-1", ts: "2026-09-24T02:36:00+0800", event: { type: "log", message: "Job 'research/demo' started by admin" } },
    { run_id: "run-1", ts: "2026-09-24T02:36:01+0800", event: { type: "progress", phase: "researching" } },
    { run_id: "run-1", ts: "2026-09-24T02:39:10+0800", event: { type: "status", status: "success" } },
  ],
  live: false,
};

function mockApi(overrides: { logs?: unknown; job?: unknown } = {}) {
  const calls: string[] = [];
  const fn = vi.fn(async (url: string) => {
    const path = String(url);
    calls.push(path);
    if (path.includes("/stream")) {
      return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
    }
    if (path.includes("/history")) return jsonResponse([]);
    if (path.includes("/logs")) return jsonResponse(overrides.logs ?? PERSISTED);
    if (path.includes("/api/jobs/")) return jsonResponse(overrides.job ?? JOB);
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/jobs/research%2Fdemo"]}>
      <Routes>
        <Route path="/jobs/:name" element={<JobDetailPage />} />
      </Routes>
    </MemoryRouter>
  );
}

beforeEach(() => {
  vi.stubGlobal("EventSource", class {
    onopen: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    onerror: (() => void) | null = null;
    close() {}
  } as unknown as typeof EventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("JobDetail persisted logs", () => {
  it("replays the previous run log when idle", async () => {
    const calls = mockApi();
    renderPage();

    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("日志"));

    await waitFor(() => {
      expect(screen.getByText("上次运行日志")).toBeTruthy();
    });
    expect(screen.getByText("Job 'research/demo' started by admin")).toBeTruthy();
    expect(screen.getByText("researching")).toBeTruthy();
    expect(calls.some((c) => c.includes("/logs"))).toBe(true);
  });

  it("labels the persisted run with status and event count", async () => {
    mockApi();
    renderPage();
    fireEvent.click(await screen.findByText("日志"));

    await waitFor(() => expect(screen.getByText(/3 条事件/)).toBeTruthy());
    expect(screen.getByText("2026-09-24T02:36:00+0800")).toBeTruthy();
  });

  it("does not show the panel when there is no persisted log", async () => {
    mockApi({ logs: { runs: [], events: [], run: null, live: false } });
    renderPage();
    fireEvent.click(await screen.findByText("日志"));

    await waitFor(() => {
      expect(screen.getByText(/当前没有正在运行的实例/)).toBeTruthy();
    });
    expect(screen.queryByText("上次运行日志")).toBeNull();
  });

  it("refresh button reloads persisted logs", async () => {
    const calls = mockApi();
    renderPage();
    fireEvent.click(await screen.findByText("日志"));
    await waitFor(() => expect(screen.getByText("上次运行日志")).toBeTruthy());

    const before = calls.filter((c) => c.includes("/logs")).length;
    fireEvent.click(screen.getByText("刷新"));
    await waitFor(() => {
      expect(calls.filter((c) => c.includes("/logs")).length).toBeGreaterThan(before);
    });
  });
});
