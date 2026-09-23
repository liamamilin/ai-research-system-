import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TemplateGrid } from "./TemplateGrid";
import { TemplateLibrary } from "./TemplateLibrary";
import type { JobTemplate } from "@/api";

function template(over: Partial<JobTemplate> = {}): JobTemplate {
  return {
    key: "_daily",
    name: "daily",
    filename: "_daily.yaml",
    label: "每日情报速报",
    category: "monitoring",
    description: "24 小时窗口速报",
    builtin: true,
    variables: ["name", "keywords", "date"],
    output_template: "output/monitoring/{date}_{name}.md",
    updated_at: "2026-09-24 08:00:00",
    prompt: "简报 {name}，关键词 {keywords}，日期 {date}。",
    keywords: ["ai", "agent"],
    language: "zh",
    timeout_seconds: 1800,
    schedule: { type: "daily", time: "08:00" },
    content: "name: x",
    ...over,
  };
}

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return {
    ok,
    status,
    statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("TemplateGrid", () => {
  it("filters by search query and category", () => {
    const templates = [
      template(),
      template({ key: "_deep", filename: "_deep.yaml", label: "深度专题研究", category: "research", description: "双源验证" }),
    ];
    render(<TemplateGrid templates={templates} />);

    expect(screen.getByText("每日情报速报")).toBeTruthy();
    expect(screen.getByText("深度专题研究")).toBeTruthy();

    fireEvent.click(screen.getByTitle("筛选：深度研究"));
    expect(screen.queryByText("每日情报速报")).toBeNull();

    fireEvent.click(screen.getByTitle("筛选：全部"));
    fireEvent.change(screen.getByPlaceholderText("搜索模板（名称/描述）..."), { target: { value: "双源" } });
    expect(screen.getByText("深度专题研究")).toBeTruthy();
    expect(screen.queryByText("每日情报速报")).toBeNull();
  });

  it("selects a template and shows preview with full prompt", () => {
    const onSelect = vi.fn();
    render(<TemplateGrid templates={[template()]} onSelect={onSelect} selectedKey="_daily" />);

    fireEvent.click(screen.getByText("每日情报速报"));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ key: "_daily" }));

    fireEvent.click(screen.getByText("预览"));
    expect(screen.getByText(/简报 \{name\}/)).toBeTruthy();
    fireEvent.click(screen.getByText("关闭"));
    expect(screen.queryByText(/简报 \{name\}/)).toBeNull();
  });

  it("hides delete for built-in templates only", () => {
    render(
      <TemplateGrid
        templates={[template(), template({ key: "mine", filename: "mine.yaml", label: "我的模板", builtin: false })]}
        onDelete={vi.fn()}
        onEdit={vi.fn()}
      />
    );
    const deletes = screen.getAllByText("删除");
    expect(deletes).toHaveLength(1);
  });

  it("renders gracefully when a template entry is malformed", () => {
    render(<TemplateGrid templates={[{ key: "x" } as JobTemplate]} />);
    expect(screen.getByText("x")).toBeTruthy();
  });
});

describe("TemplateLibrary", () => {
  const calls: string[] = [];

  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method || "GET";
        calls.push(`${method} ${url}`);
        const path = String(url);
        if (path.includes("/api/jobs/templates")) {
          return jsonResponse({ templates: [template(), template({ key: "mine", filename: "mine.yaml", label: "我的模板", builtin: false })] });
        }
        if (path.includes("/api/jobs/") && !path.includes("templates")) {
          return jsonResponse({ prompt: "来自 job 的 prompt {name}", keywords: ["x"], description: "d", output_template: "output/{date}.md" });
        }
        if (path === "/api/jobs" || path === "/api/jobs/") {
          return jsonResponse([{ name: "research/demo" }, { name: "monitoring/daily" }]);
        }
        return jsonResponse({});
      })
    );
  });

  it("opens with template count and creates a new template", async () => {
    render(<TemplateLibrary open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/共 2 个/)).toBeTruthy();
    });

    fireEvent.click(screen.getByText("+ 新建模板"));
    fireEvent.change(screen.getByPlaceholderText("例：竞品动态周报"), { target: { value: "竞品周报" } });
    fireEvent.change(screen.getByPlaceholderText("competitor-weekly"), { target: { value: "competitor-weekly" } });
    fireEvent.change(
      screen.getByPlaceholderText(/你是 \{name\}/),
      { target: { value: "分析 {name} 的竞品动态，关键词 {keywords}，日期 {date}，需给出结论与证据。" } }
    );
    fireEvent.click(screen.getByText("创建模板"));

    await waitFor(() => {
      expect(calls.some((c) => c.startsWith("POST /api/jobs/templates"))).toBe(true);
    });
  });

  it("rejects an invalid key client-side", async () => {
    render(<TemplateLibrary open onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/共 2 个/)).toBeTruthy());

    fireEvent.click(screen.getByText("+ 新建模板"));
    fireEvent.change(screen.getByPlaceholderText("例：竞品动态周报"), { target: { value: "周报" } });
    fireEvent.change(screen.getByPlaceholderText("competitor-weekly"), { target: { value: "Bad Key!" } });
    fireEvent.change(screen.getByPlaceholderText(/你是 \{name\}/), { target: { value: "内容足够长的 prompt 模板文本 {name} {keywords}" } });
    fireEvent.click(screen.getByText("创建模板"));

    expect(screen.getByText(/key 只能是小写字母/)).toBeTruthy();
    expect(calls.some((c) => c.startsWith("POST"))).toBe(false);
  });

  it("imports an existing job into the form", async () => {
    render(<TemplateLibrary open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/共 2 个/)).toBeTruthy();
    });

    const select = screen.getByDisplayValue("从现有 job 导入…");
    fireEvent.change(select, { target: { value: "research/demo" } });

    await waitFor(() => {
      expect(screen.getByDisplayValue("来自 job 的 prompt {name}")).toBeTruthy();
    });
  });

  it("deletes a user template after confirm", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<TemplateLibrary open onClose={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText(/共 2 个/)).toBeTruthy();
    });

    fireEvent.click(screen.getByText("删除"));
    await waitFor(() => {
      expect(calls.some((c) => c.startsWith("DELETE /api/jobs/templates/mine"))).toBe(true);
    });
    confirmSpy.mockRestore();
  });

  it("uses a picked template via onUse", async () => {
    const onUse = vi.fn();
    const onClose = vi.fn();
    render(<TemplateLibrary open onClose={onClose} onUse={onUse} />);
    await waitFor(() => expect(screen.getByText(/共 2 个/)).toBeTruthy());

    fireEvent.click(screen.getByText("我的模板"));
    expect(onUse).toHaveBeenCalledWith(expect.objectContaining({ key: "mine" }));
    expect(onClose).toHaveBeenCalled();
  });
});
