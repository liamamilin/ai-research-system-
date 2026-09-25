import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SchedulerPage } from "./Scheduler";

const JOBS = {
  jobs: [
    {
      name: "practical_ai_intelligence/01_model_and_pricing_radar",
      label: "Model and Pricing Radar",
      description: "track model pricing changes",
      enabled: true,
      schedule_type: "daily",
      command: "cd /proj && python run.py practical_ai_intelligence/01_model_and_pricing_radar >> logs/cron_x.log 2>&1",
    },
    {
      name: "daily_ai_agents",
      label: "Daily AI Agents",
      description: "daily agents news",
      enabled: false,
      schedule_type: "manual",
      command: "cd /proj && python run.py daily_ai_agents >> logs/cron_y.log 2>&1",
    },
  ],
  project_dir: "/proj",
  python: "/proj/.AI_research/bin/python",
};

function jsonResponse(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch(jobs = JOBS) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    calls.push({ url: path, init });
    if (path.includes("/preview")) return jsonResponse({ schedule: "0 8 * * *", timezone: "Asia/Shanghai", next_runs: ["2026-09-26T08:00:00+08:00"] });
    if (path.includes("/api/scheduler/jobs")) return jsonResponse(jobs);
    if (path.includes("/api/scheduler") && init?.method === "POST") {
      return jsonResponse({ ok: true });
    }
    if (path.includes("/api/scheduler")) return jsonResponse({ jobs: [], total: 0 });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

beforeEach(() => {
  mockFetch();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Scheduler job picker", () => {
  it("creates a durable job plan with an automatic ID and no shell command input", async () => {
    const calls = mockFetch();
    render(<SchedulerPage />);
    fireEvent.click(screen.getByText(/新建调度/));

    await waitFor(() => {
      expect(screen.getByText("Model and Pricing Radar")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Model and Pricing Radar"));

    expect(screen.queryByPlaceholderText("如: cd /path && python run.py daily_ai_agents")).toBeNull();
    const create = screen.getByRole('button', {name: '创建调度'}) as HTMLButtonElement;
    await waitFor(() => expect(create.disabled).toBe(false));
    fireEvent.click(create);
    await waitFor(() => expect(calls.some(c => c.init?.method === 'POST')).toBe(true));
    const payload = JSON.parse(calls.find(c => c.init?.method === 'POST')!.init!.body as string);
    expect(payload.job_name).toBe(JOBS.jobs[0].name);
    expect(payload.id).toBe('');
    expect(payload.missed_policy).toBe('latest');
    expect(payload.max_retries).toBe(2);

  });

  it("filters the job list by query", async () => {
    render(<SchedulerPage />);
    fireEvent.click(screen.getByText(/新建调度/));

    await waitFor(() => {
      expect(screen.getByText("Daily AI Agents")).toBeTruthy();
    });

    fireEvent.change(screen.getByPlaceholderText("搜索 job 名称或描述…（如 radar、daily、practical）"), {
      target: { value: "pricing" },
    });

    expect(screen.queryByText("Daily AI Agents")).toBeNull();
    expect(screen.getByText("Model and Pricing Radar")).toBeTruthy();
  });

  it("marks disabled jobs", async () => {
    render(<SchedulerPage />);
    fireEvent.click(screen.getByText(/新建调度/));
    await waitFor(() => {
      expect(screen.getByText("已停用")).toBeTruthy();
    });
  });
});

describe("Scheduler missed-run banners", () => {
  const CRON_JOB = {
    id: "practical_ai_intelligence",
    minute: "0",
    hour: "6",
    day_of_month: "*",
    month: "*",
    day_of_week: "*",
    command: "bash scripts/run_practical_intelligence.sh >> logs/cron_pipeline.log 2>&1",
    enabled: true,
  };

  function mockScheduler(health: unknown) {
    const fn = vi.fn(async (url: string) => {
      const path = String(url);
      if (path.includes("/api/scheduler/jobs")) return jsonResponse(JOBS);
      if (path.includes("/api/scheduler")) {
        return jsonResponse({ jobs: [CRON_JOB], total: 1, health });
      }
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fn);
  }

  it("shows an overdue banner with the missed slot", async () => {
    mockScheduler({
      jobs: [{
        id: "practical_ai_intelligence",
        status: "overdue",
        schedule: "0 6 * * *",
        last_expected_at: "2026-09-24T06:00:00",
        last_ran_at: "2026-09-21T06:10:00",
        missed_hours: 79,
        detail: "上次应运行 09-24 06:00，但最近一次实际运行是 09-21 06:10",
      }],
      overdue: 1,
      paused: 0,
    });
    render(<SchedulerPage />);
    expect(await screen.findByText(/漏跑：practical_ai_intelligence/)).toBeTruthy();
    expect(screen.getByText(/上次应运行 09-24 06:00/)).toBeTruthy();
  });

  it("shows a paused banner without claiming a failure", async () => {
    mockScheduler({
      jobs: [{
        id: "practical_ai_intelligence",
        status: "paused",
        schedule: "0 6 * * *",
        detail: "调度已暂停，cron 不会触发该任务",
      }],
      overdue: 0,
      paused: 1,
    });
    render(<SchedulerPage />);
    expect(await screen.findByText(/已暂停：practical_ai_intelligence/)).toBeTruthy();
    expect(screen.queryByText(/漏跑/)).toBeNull();
  });

  it("stays silent when every job is on time", async () => {
    mockScheduler({
      jobs: [{ id: "practical_ai_intelligence", status: "ok", schedule: "0 6 * * *", detail: "最近一次实际运行：09-24 06:12" }],
      overdue: 0,
      paused: 0,
    });
    render(<SchedulerPage />);
    await waitFor(() => {
      expect(screen.getByText("practical_ai_intelligence")).toBeTruthy();
    });
    expect(screen.queryByText(/漏跑/)).toBeNull();
    expect(screen.queryByText(/已暂停：/)).toBeNull();
  });
});


describe("Schedule editing and previews", () => {
  it("switches selected jobs and refuses disabled ones", async () => {
    const calls = mockFetch({ ...JOBS, jobs: [...JOBS.jobs, {...JOBS.jobs[0], name: 'research/中文任务', label: '中文任务'}] });
    render(<SchedulerPage />);
    fireEvent.click(screen.getByText(/新建调度/));
    fireEvent.click(await screen.findByText("Model and Pricing Radar"));
    expect((screen.getByText('Daily AI Agents').closest('button') as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("中文任务"));
    const create = screen.getByRole('button', {name: '创建调度'}) as HTMLButtonElement;
    await waitFor(() => expect(create.disabled).toBe(false));
    fireEvent.click(create);
    await waitFor(() => expect(calls.some(c => c.init?.method === 'POST')).toBe(true));
    expect(JSON.parse(calls.find(c => c.init?.method === 'POST')!.init!.body as string).job_name).toBe('research/中文任务');
  });

  it("shows system agents without cron edit, pause or delete controls", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ jobs: [{
      id: "com.arec.pipeline.daily", title: "情报矩阵 · 每日运行", backend: "launchd", editable: false,
      enabled: true, schedule_label: "每天 06:00", next_runs: ["2026-09-26T06:00:00+08:00"], command: "ensure_round.py",
    }], timezone: "Asia/Shanghai (UTC+0800)" })));
    render(<SchedulerPage />);
    expect(await screen.findByText("情报矩阵 · 每日运行")).toBeTruthy();
    expect(screen.getByText("2026-09-26 06:00")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /暂停 com.arec/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /编辑 com.arec/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /删除 com.arec/ })).toBeNull();
  });

  it("previews a changed schedule and saves an edit with PUT", async () => {
    const calls: { url: string; method?: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), method: init?.method, body: init?.body as string });
      if (String(url).includes("/preview")) return jsonResponse({ schedule: "0 9 * * *", timezone: "Asia/Shanghai", next_runs: ["2026-09-26T09:00:00+08:00"] });
      if (init?.method === "PUT") return jsonResponse({ ok: true });
      return jsonResponse({ jobs: [{ id: "report", minute: "0", hour: "8", day_of_month: "*", month: "*", day_of_week: "*", command: "echo report", enabled: false }] });
    }));
    render(<SchedulerPage />);
    fireEvent.click(await screen.findByRole("button", { name: "编辑 report" }));
    fireEvent.change(screen.getByLabelText("执行时间"), { target: { value: "09:00" } });
    await screen.findByText("2026-09-26 09:00");
    expect(screen.getByText(/当前已暂停/)).toBeTruthy();
    fireEvent.click(screen.getByText("保存修改"));
    await waitFor(() => expect(calls.some(c => c.method === "PUT" && c.url === "/api/scheduler/report")).toBe(true));
    const body = JSON.parse(calls.find(c => c.method === "PUT")!.body!);
    expect(body.schedule).toBe("0 9 * * *");
    expect(body.command).toBe("echo report");
  });
});
