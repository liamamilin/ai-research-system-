import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {ScheduleRuns} from './ScheduleRuns';

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
it('shows persisted outcomes and retry reasons in the schedule timezone', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, text: async () => JSON.stringify({runs: [{
    id: 'run', due_at_iso: '2026-09-25T08:00:00+08:00', started_at_iso: '2026-09-25T08:01:03+08:00',
    status: 'success', attempt: 2, output_path: '/project/output/report.md',
    timezone: 'Asia/Shanghai',
    attempts: [{attempt: 1, status: 'failed', error: '连接超时'}, {attempt: 2, status: 'success'}],
  }]})})));
  render(<ScheduleRuns id="plan" />);
  // The offset stays on screen: the same wall clock happens twice across a DST
  // change, and "08:00" alone does not say which one this is.
  expect(await screen.findByText('计划 2026-09-25 08:00:00 UTC+08:00')).toBeTruthy();
  expect(screen.getByText('实际开始 2026-09-25 08:01:03 UTC+08:00')).toBeTruthy();
  expect(screen.getByText(/第 1 次：失败（连接超时）/)).toBeTruthy();
  expect(screen.getByText('报告：/project/output/report.md')).toBeTruthy();
  expect(screen.getByText(/时区 Asia\/Shanghai/)).toBeTruthy();
});

it('labels a UTC timestamp as UTC', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: true, text: async () => JSON.stringify({runs: [{
    id: 'run', due_at_iso: '2026-09-25T00:00:00Z', status: 'success', attempt: 1, attempts: [],
  }]})})));
  render(<ScheduleRuns id="plan" />);
  expect(await screen.findByText('计划 2026-09-25 00:00:00 UTC')).toBeTruthy();
});

it('surfaces a failed history load instead of showing an empty list', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ok: false, status: 503,
    text: async () => JSON.stringify({error: {code: 'offline', message: '执行器离线', details: {}}})})));
  render(<ScheduleRuns id="plan" />);
  const alert = await screen.findByRole('alert');
  expect(alert.textContent).toContain('执行器离线');
  expect(screen.queryByText(/还没有到期执行记录/)).toBeNull();
});
