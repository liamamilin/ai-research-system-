import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobDetailPage } from "./JobDetail";
import { useAuthStore } from "@/lib/auth-store";

vi.mock("@/components/YamlEditor", () => ({
  YamlEditor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea aria-label="yaml" value={value} onChange={(e) => onChange?.(e.target.value)} />
  ),
}));

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

/** Every EventSource the page builds, so a test can assert one was built. */
let streams: FakeStream[] = [];

class FakeStream {
  onopen: ((e: Event) => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    streams.push(this);
  }
  close() {
    this.closed = true;
  }
  /* test helpers */
  accept() {
    this.onopen?.(new Event("open"));
  }
  emit(payload: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
}

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockApi(jobOverrides: Record<string, unknown> = {}) {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = String(url);
      calls.push(path);
      if (path.includes("/validate")) return jsonResponse({ ok: true, errors: [], warnings: [] });
      if (path.includes("/stream")) {
        return new Response("", { status: 200, headers: { "content-type": "text/event-stream" } });
      }
      if (path.includes("/history")) return jsonResponse([]);
      if (path.includes("/logs")) return jsonResponse({ runs: [], events: [], run: null, live: false });
      if (path.endsWith("/run")) return jsonResponse({ ok: true });
      if (path.includes("/api/jobs/")) return jsonResponse({ ...JOB, ...jobOverrides });
      return jsonResponse({});
    })
  );
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
  streams = [];
  vi.stubGlobal("EventSource", FakeStream as unknown as typeof EventSource);
  useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("JobDetail log stream is gated by the tab", () => {
  it("subscribes when the log tab is opened, not only after 运行", async () => {
    mockApi();
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    expect(streams).toHaveLength(0);

    fireEvent.click(screen.getByText("日志"));

    await waitFor(() => expect(streams).toHaveLength(1));
    expect(streams[0].url).toBe("/api/jobs/research%2Fdemo/stream");
  });

  it("keeps working after the pre-run subscription reports idle", async () => {
    // The regression: a run subscribes before the task exists server-side and is
    // told "idle". That used to clear the enable flag, so the panel kept
    // rendering "等待日志..." with no stream behind it, and 重新连接 could not
    // recover because it re-subscribed without re-enabling.
    mockApi();
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());

    fireEvent.click(screen.getByText("运行"));

    // First subscription, opened before the task is registered server-side.
    await waitFor(() => expect(streams.length).toBeGreaterThan(0));
    streams[0].accept();
    streams[0].emit({ type: "status", status: "idle" });

    // handleRun re-subscribes once the task exists; that new stream must exist.
    await waitFor(() => expect(streams.length).toBeGreaterThan(1));
    const live = streams[streams.length - 1];
    expect(live.closed).toBe(false);

    live.accept();
    live.emit({ type: "log", level: "info", message: "Research round 1/12" });

    expect(await screen.findByText("Research round 1/12")).toBeTruthy();
    // And the waiting state is gone.
    expect(screen.queryByText("等待日志...")).toBeNull();
  });

  it("reconnects from the log panel's own button", async () => {
    // The reported state: the job is running per the API, the stream has not
    // delivered anything yet, so the panel waits. This is the only state the
    // retry button belongs to -- a stream that has already produced events
    // shows them, and one that errored outright shows its own failure card.
    mockApi({ is_running: true });
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("日志"));
    await waitFor(() => expect(streams).toHaveLength(1));

    const retry = await screen.findByText("重新连接日志流");
    fireEvent.click(retry);

    await waitFor(() => expect(streams.length).toBeGreaterThan(1));
    expect(streams[streams.length - 1].url).toBe("/api/jobs/research%2Fdemo/stream");
  });
});
