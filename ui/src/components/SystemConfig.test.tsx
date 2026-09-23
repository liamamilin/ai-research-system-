import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SystemConfig } from "./SystemConfig";

const PARSED = {
  ai: { model: "old-model", base_url: "https://x/v1", api_key_env: "LLM_API_KEY" },
  search: { provider: "parallel", max_results: 10, api_key_env: "PARALLEL_API_KEY" },
  research: { mode: "auto", max_rounds: 12 },
  budget: { monthly_usd_limit: 0 },
  notifications: { enabled: false, notify_on: ["failed"] },
};

const SECRETS = {
  secrets: [
    { env: "LLM_API_KEY", label: "AI 模型（LLM）", configured: true, masked: "sk-1****ab" },
    { env: "PARALLEL_API_KEY", label: "搜索服务", configured: false, masked: "" },
  ],
};

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status < 300,
    status,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockFetch() {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (String(url).includes("/api/config/secrets")) {
      if (init?.method === "PUT") {
        return jsonResponse(200, { ok: true, secrets: SECRETS.secrets });
      }
      return jsonResponse(200, SECRETS);
    }
    if (String(url).includes("/api/config/test-")) {
      return jsonResponse(200, { ok: true, latency_ms: 42, model: "old-model" });
    }
    if (init?.method === "PUT") {
      return jsonResponse(200, { ok: true, mtime: 123 });
    }
    return jsonResponse(200, {
      content: "ai:\n  model: old-model\n",
      parsed: PARSED,
      mtime: 100,
    });
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SystemConfig form", () => {
  it("renders parsed values into the form", async () => {
    mockFetch();
    render(<SystemConfig />);
    expect(await screen.findByDisplayValue("old-model")).toBeTruthy();
    expect(screen.getByDisplayValue("https://x/v1")).toBeTruthy();
    expect(screen.getByText("API 密钥")).toBeTruthy();
    expect(screen.getByText("预算护栏")).toBeTruthy();
  });

  it("shows configured API keys masked", async () => {
    mockFetch();
    render(<SystemConfig />);
    expect(await screen.findByPlaceholderText("已配置 · sk-1****ab")).toBeTruthy();
    expect(screen.getByPlaceholderText("未配置")).toBeTruthy();
  });

  it("saves changed fields as a patch with mtime", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const modelInput = await screen.findByDisplayValue("old-model");
    fireEvent.change(modelInput, { target: { value: "new-model" } });
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => {
      expect(calls.some((c) => c.init?.method === "PUT")).toBe(true);
    });
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.ai.model).toBe("new-model");
    expect(body.patch.search.provider).toBe("parallel");
    expect(body.patch.research.mode).toBe("auto");
    expect(body.expected_mtime).toBe(100);
  });

  it("writes typed API keys through the secrets endpoint", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const input = await screen.findByPlaceholderText("已配置 · sk-1****ab");
    fireEvent.change(input, { target: { value: "typed-key" } });
    fireEvent.click(screen.getByText("保存密钥"));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/secrets") && c.init?.method === "PUT"))
        .toBe(true);
    });
    const put = calls.find((c) => c.url.includes("/secrets") && c.init?.method === "PUT");
    const body = JSON.parse(String(put?.init?.body));
    expect(body.values.LLM_API_KEY).toBe("typed-key");
  });

  it("tests the LLM with the current form values", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const modelInput = await screen.findByDisplayValue("old-model");
    fireEvent.change(modelInput, { target: { value: "form-model" } });
    fireEvent.click(screen.getByText("测试 LLM"));

    await waitFor(() => {
      expect(calls.some((c) => c.url.includes("/test-llm"))).toBe(true);
    });
    const call = calls.find((c) => c.url.includes("/test-llm"));
    const body = JSON.parse(String(call?.init?.body));
    expect(body.config.ai.model).toBe("form-model");
    expect(await screen.findByText(/可用/)).toBeTruthy();
  });

  it("includes notification toggles in the patch", async () => {
    const calls = mockFetch();
    render(<SystemConfig />);

    const toggle = await screen.findByLabelText("启用通知");
    fireEvent.click(toggle);
    fireEvent.click(screen.getByText("保存配置"));

    await waitFor(() => {
      expect(calls.some((c) => c.init?.method === "PUT")).toBe(true);
    });
    const put = calls.find((c) => c.init?.method === "PUT" && !c.url.includes("secrets"));
    const body = JSON.parse(String(put?.init?.body));
    expect(body.patch.notifications.enabled).toBe(true);
  });
});
