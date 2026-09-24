import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeleteJobDialog } from "./DeleteJobDialog";

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status < 400, status, statusText: "ok",
    text: async () => JSON.stringify(body),
  } as Response;
}

function mockDelete(impl?: (url: string, init?: RequestInit) => Response) {
  const fn = vi.fn(async (url: string, init?: RequestInit) =>
    impl ? impl(String(url), init) : jsonResponse({ ok: true, deleted: "a.yaml", backup_path: "/tmp/backups/a.yaml" }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("DeleteJobDialog", () => {
  it("names the file and the backup location before deleting", () => {
    mockDelete();
    render(<DeleteJobDialog jobName="research/demo" onClose={() => {}} />);
    expect(screen.getByText("jobs/research/demo.yaml")).toBeTruthy();
    expect(screen.getByText(/state\/backups/)).toBeTruthy();
  });

  it("does not call the API until the user confirms", async () => {
    const fn = mockDelete();
    render(<DeleteJobDialog jobName="research/demo" onClose={() => {}} />);

    fireEvent.click(screen.getByText("取消"));
    expect(fn).not.toHaveBeenCalled();

    render(<DeleteJobDialog jobName="research/demo" onClose={() => {}} />);
    fireEvent.click(screen.getAllByText("删除").at(-1) as HTMLElement);
    await waitFor(() => expect(fn).toHaveBeenCalled());
    const [url, init] = fn.mock.calls[0];
    expect(String(url)).toContain("research%2Fdemo");
    expect((init as RequestInit).method).toBe("DELETE");
  });

  it("reports the backup path after a successful delete", async () => {
    const onDeleted = vi.fn();
    mockDelete();
    render(<DeleteJobDialog jobName="research/demo" onDeleted={onDeleted} onClose={() => {}} />);

    fireEvent.click(screen.getByText("删除"));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("research/demo", "/tmp/backups/a.yaml"));
  });

  it("blocks deletion while the job is running", () => {
    const fn = mockDelete();
    render(<DeleteJobDialog jobName="research/demo" isRunning onClose={() => {}} />);

    expect(screen.getByText(/正在运行/)).toBeTruthy();
    const button = screen.getByText("删除") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(fn).not.toHaveBeenCalled();
  });

  it("warns before deleting a pipeline stage", () => {
    mockDelete();
    render(
      <DeleteJobDialog
        jobName="practical_ai_intelligence/01_radar"
        warnings={["这是情报流水线的阶段之一，删除后定时任务的下一次运行会在该阶段失败。"]}
        onClose={() => { }}
      />,
    );
    expect(screen.getByText(/情报流水线的阶段/)).toBeTruthy();
  });

  it("shows the server's refusal instead of closing", async () => {
    mockDelete(() => jsonResponse({ error: { code: "already_running", message: "Job 正在运行，无法删除" } }, 409));
    render(<DeleteJobDialog jobName="research/demo" onClose={() => {}} />);

    fireEvent.click(screen.getByText("删除"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText("Job 正在运行，无法删除")).toBeTruthy();
  });

  it("renders nothing without a job", () => {
    mockDelete();
    const { container } = render(<DeleteJobDialog jobName={null} onClose={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
