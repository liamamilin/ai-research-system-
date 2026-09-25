import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {ScheduleRuns} from './ScheduleRuns';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it('shows persisted outcomes and retry reasons in the schedule timezone', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, text: async () => JSON.stringify({runs: [{
    id: 'run', due_at_iso: '2026-09-25T08:00:00+08:00', started_at_iso: '2026-09-25T08:01:03+08:00',
    status: 'success', attempt: 2, output_path: '/project/output/report.md',
    attempts: [{attempt: 1, status: 'failed', error: '连接超时'}, {attempt: 2, status: 'success'}],
  }]})})));
  render(<ScheduleRuns id="plan" />);
  expect(await screen.findByText('计划 2026-09-25 08:00:00')).toBeTruthy();
  expect(screen.getByText('实际开始 2026-09-25 08:01:03')).toBeTruthy();
  expect(screen.getByText(/第 1 次：失败（连接超时）/)).toBeTruthy();
  expect(screen.getByText('报告：/project/output/report.md')).toBeTruthy();
});
