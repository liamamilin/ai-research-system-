import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { AskPage } from "./Ask";
import { useAuthStore } from "@/lib/auth-store";

interface Deferred {
  resolve: (value: unknown) => void;
  reject: (err: unknown) => void;
  promise: Promise<unknown>;
}

function deferred(): Deferred {
  let resolve!: (value: unknown) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { resolve, reject, promise };
}

const ANSWER = (text: string, citations: unknown[] = []) => ({
  answer: text,
  citations,
  mode: "keyword",
  usage: { total_tokens: 0 },
});

let pendingCalls: Array<{ question: string; deferred: Deferred }> = [];

function mockAsk(answers: Deferred[]) {
  pendingCalls = [];
  const fn = vi.fn(async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || "{}"));
    const slot = answers[pendingCalls.length] || answers[answers.length - 1];
    const entry = { question: body.question, deferred: slot };
    pendingCalls.push(entry);
    const data = await slot.promise;
    return {
      ok: true,
      status: 200,
      statusText: "ok",
      text: async () => JSON.stringify(data),
    } as Response;
  });
  vi.stubGlobal("fetch", fn);
}

function renderAsk() {
  return render(
    <MemoryRouter>
      <AskPage />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useAuthStore.setState({ user: { username: "a", role: "admin" } as never });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("AskPage", () => {
  it("shows a visible in-flight state with the pending question", async () => {
    const slot = deferred();
    mockAsk([slot]);

    renderAsk();
    fireEvent.change(screen.getByPlaceholderText(/跨全部报告提问/), { target: { value: "最近的模型价格变化" } });
    fireEvent.click(screen.getByText("提问"));

    expect(await screen.findByText(/正在检索报告并生成回答/)).toBeTruthy();
    expect(screen.getByText("最近的模型价格变化")).toBeTruthy();
  });

  it("ignores a stale response that arrives after a newer question", async () => {
    const first = deferred();
    const second = deferred();
    mockAsk([first, second]);

    renderAsk();
    const input = screen.getByPlaceholderText(/跨全部报告提问/);

    fireEvent.change(input, { target: { value: "第一个问题" } });
    fireEvent.click(screen.getByText("提问"));

    // While the first request is in flight the submit button is disabled, but
    // the example chips stay clickable — that is the real race.
    fireEvent.click(screen.getByText("模型定价有什么变化，影响哪些工作流？"));

    // The newer question answers first…
    expect(pendingCalls.map((c) => c.question)).toEqual([
      "第一个问题", "模型定价有什么变化，影响哪些工作流？",
    ]);
    second.resolve(ANSWER("第二个问题的答案", [{ index: 1, path: "b.md", title: "B", source: "b", snippet: "" }]));
    await waitFor(() => expect(screen.getByText("第二个问题的答案")).toBeTruthy());

    // …then the older one arrives late and must be discarded.
    first.resolve(ANSWER("第一个问题的答案"));
    await waitFor(() => expect(screen.queryByText("第一个问题的答案")).toBeNull());
    expect(screen.getByText("第二个问题的答案")).toBeTruthy();
  });

  it("warns when retrieval returned no citations", async () => {
    const slot = deferred();
    mockAsk([slot]);

    renderAsk();
    fireEvent.change(screen.getByPlaceholderText(/跨全部报告提问/), { target: { value: "库里没有的东西" } });
    fireEvent.click(screen.getByText("提问"));
    slot.resolve(ANSWER("一个没有依据的回答"));

    expect(await screen.findByText(/没有命中任何报告/)).toBeTruthy();
  });

  it("does not show the empty-citation warning when sources exist", async () => {
    const slot = deferred();
    mockAsk([slot]);

    renderAsk();
    fireEvent.change(screen.getByPlaceholderText(/跨全部报告提问/), { target: { value: "价格变化" } });
    fireEvent.click(screen.getByText("提问"));
    slot.resolve(ANSWER("有依据的回答", [{ index: 1, path: "a.md", title: "A", source: "a", snippet: "" }]));

    await screen.findByText("有依据的回答");
    expect(screen.queryByText(/没有命中任何报告/)).toBeNull();
    expect(screen.getByText(/引用（1）/)).toBeTruthy();
  });

  it("keeps the error visible and clears the spinner when the request fails", async () => {
    const slot = deferred();
    mockAsk([slot]);

    renderAsk();
    fireEvent.change(screen.getByPlaceholderText(/跨全部报告提问/), { target: { value: "会失败的问题" } });
    fireEvent.click(screen.getByText("提问"));
    slot.reject(new Error("HTTP 500"));

    expect(await screen.findByText("HTTP 500")).toBeTruthy();
    expect(screen.queryByText(/正在检索报告并生成回答/)).toBeNull();
  });
});
