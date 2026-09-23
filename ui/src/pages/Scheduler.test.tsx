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
