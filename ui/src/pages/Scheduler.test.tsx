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

function mockFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url);
    calls.push({ url: path, init });
    if (path.includes("/api/scheduler/jobs")) return jsonResponse(JOBS);
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
  it("lists jobs and fills id + command when one is picked", async () => {
    render(<SchedulerPage />);
    fireEvent.click(screen.getByText(/新建调度/));

    await waitFor(() => {
      expect(screen.getByText("Model and Pricing Radar")).toBeTruthy();
    });
    fireEvent.click(screen.getByText("Model and Pricing Radar"));

    const idInput = screen.getByPlaceholderText("如: daily_report, weekly_summary") as HTMLInputElement;
    const commandInput = screen.getByPlaceholderText(
      "如: cd /path && python run.py daily_ai_agents",
    ) as HTMLInputElement;

    expect(idInput.value).toBe("practical_ai_intelligence_01_model_and_pricing_radar");
    expect(commandInput.value).toContain("run.py practical_ai_intelligence/01_model_and_pricing_radar");
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
    expect(screen.queryByText(/已暂停/)).toBeNull();
  });
});
