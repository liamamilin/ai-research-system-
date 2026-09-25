import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CronBuilder, scheduleSummary } from "./CronBuilder";

afterEach(cleanup);
it("keeps a complex cron expression intact in advanced mode", () => {
  const onChange = vi.fn();
  render(<CronBuilder value="0,30 9-17 * * mon,wed,fri" onChange={onChange} />);
  expect((screen.getByLabelText(/Cron 表达式/) as HTMLInputElement).value).toBe("0,30 9-17 * * mon,wed,fri");
  expect(onChange).not.toHaveBeenCalled();
});
it("changes weekdays without losing the selected time", () => {
  const onChange = vi.fn();
  render(<CronBuilder value="45 9 * * *" onChange={onChange} />);
  fireEvent.click(screen.getByRole("button", { name: "工作日" }));
  expect(onChange).toHaveBeenCalledWith("45 9 * * 1-5");
});
it("warns when a month may lack the selected day", () => {
  render(<CronBuilder value="0 8 31 * *" onChange={() => {}} />);
  expect(screen.getByText(/没有 31 日的月份/)).toBeTruthy();
  expect(scheduleSummary("0 */6 * * *")).toBe("每 6 小时（从 00:00 起）");
});
