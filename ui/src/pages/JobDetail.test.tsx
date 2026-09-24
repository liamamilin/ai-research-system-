import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { JobDetailPage } from "./JobDetail";
import { useAuthStore } from "@/lib/auth-store";

// CodeMirror does not render a usable editor in jsdom; the YAML preflight and
// the unsaved-changes guard are what these tests are about.
vi.mock("@/components/YamlEditor", () => ({
  YamlEditor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea
      aria-label="yaml"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
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

function mockApi(overrides: { logs?: unknown; job?: unknown; validation?: unknown } = {}) {
  const calls: string[] = [];
  const fn = vi.fn(async (url: string) => {
    const path = String(url);
    calls.push(path);
    if (path.includes("/validate")) {
      return jsonResponse(overrides.validation ?? { ok: true, errors: [], warnings: [] });
    }
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

describe("JobDetail YAML preflight", () => {
  beforeEach(() => {
    // Preflight and the unsaved-changes guard are editor-only behaviour.
    useAuthStore.setState({ user: { username: "e", role: "editor" } as never });
  });

  it("shows blocking errors before the user attempts a save", async () => {
    mockApi({
      validation: {
        ok: false,
        errors: ["YAML 解析失败: 第 2 行", "prompt 为空：该 job 无法生成报告"],
        warnings: [],
      },
    });
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("YAML 配置"));

    const editor = await screen.findByLabelText("yaml");
    fireEvent.change(editor, { target: { value: "name: [broken" } });

    expect(await screen.findByText(/保存会被拒绝/, undefined, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText("YAML 解析失败: 第 2 行")).toBeTruthy();
    expect(screen.getByText("prompt 为空：该 job 无法生成报告")).toBeTruthy();
  });

  it("confirms a clean document", async () => {
    mockApi({ validation: { ok: true, errors: [], warnings: ["keywords 建议至少 3 个"] } });
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("YAML 配置"));

    const editor = await screen.findByLabelText("yaml");
    // must differ from the loaded document, otherwise there is nothing to check
    fireEvent.change(editor, { target: { value: 'name: "demo2"\nprompt: "x"\n' } });

    // the preflight is debounced
    expect(await screen.findByText(/预检通过/, undefined, { timeout: 3000 })).toBeTruthy();
    expect(screen.getByText(/keywords 建议至少 3 个/)).toBeTruthy();
  });

  it("does not validate before anything is edited", async () => {
    const calls = mockApi();
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("YAML 配置"));
    await screen.findByLabelText("yaml");
    expect(calls.some((c) => c.includes("/validate"))).toBe(false);
  });

  it("blocks leaving the YAML tab with unsaved edits", async () => {
    mockApi();
    renderPage();
    await waitFor(() => expect(screen.getByText("YAML 配置")).toBeTruthy());
    fireEvent.click(screen.getByText("YAML 配置"));

    const editor = await screen.findByLabelText("yaml");
    fireEvent.change(editor, { target: { value: "name: unsaved" } });
    fireEvent.click(screen.getByText("日志"));

    expect(await screen.findByText(/有未保存的修改，离开会丢失/)).toBeTruthy();

    fireEvent.click(screen.getByText("继续编辑"));
    expect(screen.queryByText(/有未保存的修改，离开会丢失/)).toBeNull();
    // the inline "unsaved" badge stays: the edit is still there
    expect(screen.getByText("有未保存的修改")).toBeTruthy();

    fireEvent.click(screen.getByText("日志"));
    fireEvent.click(await screen.findByText("放弃修改并离开"));
    // back on the overview tab, and the edit was discarded
    await waitFor(() => expect(screen.queryByLabelText("yaml")).toBeNull());
    expect(screen.queryByText(/有未保存的修改，离开会丢失/)).toBeNull();
    expect(screen.queryByText("有未保存的修改")).toBeNull();
  });
});
