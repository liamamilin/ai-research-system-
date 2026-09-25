"""Product-facing operations for reliable job schedules."""
from __future__ import annotations

from datetime import datetime
import re

from core import cron, schedule_host
from core.schedule_runner import Runtime, canonical_job
from core.schedule_store import ScheduleStore, ScheduleConflict, iso, zone
from web.settings import get_settings


def runtime() -> Runtime:
    return Runtime.from_settings(get_settings())


def store() -> ScheduleStore:
    return ScheduleStore(runtime().state_dir)


def ensure_online() -> dict:
    return schedule_host.install(runtime())


def save(job_name: str, expression: str, *, schedule_id: str | None = None,
         timezone: str = 'local', missed_policy: str = 'latest', max_retries: int = 2,
         editing: bool = False) -> dict:
    active = runtime()
    canonical, _ = canonical_job(active, job_name)
    # Validate before starting a system service or persisting any plan.
    from core.schedule_store import slot
    import time
    slot(expression, time.time(), timezone)
    if missed_policy not in ('latest', 'skip') or type(max_retries) is not int or not 0 <= max_retries <= 5:
        raise ValueError('补跑或重试策略无效')
    if schedule_id and not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', schedule_id):
        raise ValueError('调度 ID 格式无效')
    if not editing:
        from web.services import scheduler
        if schedule_id and any(j['id'] == schedule_id for j in scheduler.all_jobs()):
            raise ScheduleConflict('调度 ID 已被旧系统计划占用，请使用其他标识')
        if any(p['job_name'] == canonical for p in store().list()):
            raise ScheduleConflict('这个 job 已有周期计划，请编辑现有计划')
    ensure_online()
    plan = store().save(canonical, expression, schedule_id=schedule_id, timezone=timezone,
                        missed_policy=missed_policy, max_retries=max_retries, editing=editing)
    return describe(plan)


def describe(plan: dict) -> dict:
    record = store().runs(plan['id'], 1)
    last = record[0] if record else None
    next_runs = cron.fire_times(cron.parse_schedule(plan['schedule']), datetime.now(zone(plan['timezone'])))
    return {**plan, **cron.parse_schedule(plan['schedule']), 'enabled': bool(plan['enabled']),
            'backend': 'managed', 'editable': True, 'title': plan['job_name'],
            'command': '', 'next_runs': [r.isoformat(timespec='seconds') for r in next_runs] if plan['enabled'] else [],
            'last_run': last, 'pending_due_at': iso(plan['next_run_at'], plan['timezone'])}


def classify(plan: dict, host: dict) -> dict:
    last = plan.get('last_run')
    state, detail = 'ok', '计划已生效，等待第一次执行'
    if not plan['enabled']:
        state, detail = 'paused', '计划已暂停；恢复后从下次计划时间开始，不补跑暂停期间的任务'
    elif not host.get('online'):
        state, detail = 'overdue', '执行器离线，计划已保留；恢复后按补跑策略处理'
    elif last:
        code = last['status']
        if code in ('failed', 'interrupted', 'blocked'):
            state, detail = 'overdue', last['error'] or '上次执行未成功，请查看执行记录'
        elif code == 'retry':
            state, detail = 'retry', f"第 {last['attempt']} 次执行未成功，将于 {iso(last['ready_at'], plan['timezone'])} 重试"
        elif code in ('queued', 'starting', 'running'):
            state, detail = 'running' if code == 'running' else 'queued', '任务正在运行' if code == 'running' else '已进入执行队列'
        elif code == 'skipped':
            state, detail = 'ok', last['error']
        else:
            state, detail = 'ok', '最近一次执行成功，报告已生成'
    return {'id': plan['id'], 'status': state, 'detail': detail, 'schedule': plan['schedule'],
            'last_ran_at': iso(last.get('started_at'), plan['timezone']) if last else None}


def preview(expression: str, timezone: str = 'local') -> dict:
    moment = datetime.now(zone(timezone))
    data = cron.preview(expression, now=moment)
    data['timezone'] = cron.timezone_label() if timezone == 'local' else f"{timezone} (UTC{moment.strftime('%z')})"
    return data


def health_report(state_dir: str) -> dict:
    """Used by global health checks, including when the Web app is idle."""
    storage = ScheduleStore(state_dir)
    plans = storage.list()
    if not plans:
        return {'status': 'ok', 'detail': '暂无托管计划', 'jobs': [], 'overdue': 0, 'paused': 0}
    host = storage.health()
    jobs = []
    for plan in plans:
        record = storage.runs(plan['id'], 1)
        jobs.append(classify({**plan, 'last_run': record[0] if record else None}, host))
    overdue = sum(j['status'] == 'overdue' for j in jobs)
    return {'status': 'error' if overdue else 'ok', 'detail': f"{len(plans)} 个托管计划；{host['detail']}",
            'jobs': jobs, 'overdue': overdue, 'paused': sum(j['status'] == 'paused' for j in jobs)}
