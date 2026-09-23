import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DashboardPage } from "./Dashboard";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status < 400,
    status,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

const USAGE = {
  days: 30,
  totals: {
    runs: 0,
    runs_with_usage: 0,
    prompt_tokens: 0,
    completion_tokens: 0,
    total_tokens: 0,
    searches: 0,
  },
  per_day: [],
  per_job: [],
  per_model: [],
};

const HEALTH_WARN = {
  status: "warn",
  checked_at: "2026-09-24T08:00:00+0800",
  deep: false,
  errors: [],
  warnings: ["vector_coverage"],
  checks: [
    { name: "config", status: "ok", detail: "model=deepseek-v4.1-flash" },
    { name: "reports_db", status: "ok", detail: "298 rows" },
    { name: "vector_coverage", status: "warn", detail: "10/298 reports lack vectors" },
  ],
};

const HEALTH_OK = {
  ...HEALTH_WARN,
  status: "ok" as const,
  warnings: [],
  checks: HEALTH_WARN.checks.slice(0, 2),
};

const HEALTH_ERROR = {
  ...HEALTH_WARN,
  status: "error" as const,
  errors: ["config"],
  checks: [...HEALTH_WARN.checks.slice(0, 2), { name: "config", status: "error", detail: "missing system.yaml" }],
};

function mockApi(health: unknown) {
  const fn = vi.fn(async (url: string) => {
    const path = String(url);
    if (path.includes("/api/health")) return jsonResponse(health, 200);
    if (path.includes("/api/jobs")) return jsonResponse([]);
    if (path.includes("/api/usage")) return jsonResponse(USAGE);
    if (path.includes("/api/reports/stats")) return jsonResponse({ total: 0, total_size_bytes: 0, categories: 0 });
    if (path.includes("/api/reports/ratings")) return jsonResponse({ items: [] });
    return jsonResponse({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Dashboard system health", () => {
  it("shows a normal badge and hides details until clicked", async () => {
    mockApi(HEALTH_OK);
    render(<MemoryRouter><DashboardPage /></MemoryRouter>);

    await waitFor(() => expect(screen.getByText(/系统 正常/)).toBeTruthy());
    expect(screen.queryByText(/vector_coverage/)).toBeNull();

    fireEvent.click(screen.getByText(/系统 正常/));
    expect(screen.getByText("reports_db")).toBeTruthy();
  });

  it("counts warnings in the badge and lists failing checks", async () => {
    mockApi(HEALTH_WARN);
    render(<MemoryRouter><DashboardPage /></MemoryRouter>);

    const badge = await screen.findByText(/系统 注意 1/);
    fireEvent.click(badge);
    expect(screen.getByText("10/298 reports lack vectors")).toBeTruthy();
  });

  it("surfaces errors distinctly", async () => {
    mockApi(HEALTH_ERROR);
    render(<MemoryRouter><DashboardPage /></MemoryRouter>);

    fireEvent.click(await screen.findByText(/系统 异常 1/));
    expect(screen.getByText("missing system.yaml")).toBeTruthy();
  });

  it("renders no health badge when the endpoint fails", async () => {
    const fn = vi.fn(async (url: string) => {
      if (String(url).includes("/api/health")) {
        return { ok: false, status: 503, statusText: "", text: async () => "{}" } as Response;
      }
      if (String(url).includes("/api/jobs")) return jsonResponse([]);
      if (String(url).includes("/api/usage")) return jsonResponse(USAGE);
      return jsonResponse({});
    });
    vi.stubGlobal("fetch", fn);

    render(<MemoryRouter><DashboardPage /></MemoryRouter>);
    await waitFor(() => expect(fn).toHaveBeenCalled());
    expect(screen.queryByText(/系统 /)).toBeNull();
  });
});
