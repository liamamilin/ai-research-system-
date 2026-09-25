"""The contract for arbitrary recurring jobs: durable, bounded, deduplicated."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import os

import pytest

from core.fileio import file_lock
from core.schedule_store import ScheduleStore, ScheduleConflict, slot
from core.schedule_runner import Runtime, dispatch_once, recover, run_lock, work, execute_job

BASE = datetime.fromisoformat('2026-09-25T07:59:30+08:00').timestamp()
DUE = BASE + 30


@pytest.fixture
def store(tmp_path):
    return ScheduleStore(str(tmp_path / 'state'))


def plan(store, **kwargs):
    return store.save('research/radar', '0 8 * * *', timezone='Asia/Shanghai', now=BASE, **kwargs)


def test_new_plan_does_not_backfill_before_creation(store):
    p = plan(store)
    assert p['next_run_at'] == DUE
    assert store.enqueue_due(BASE) == 0
    assert store.runs(p['id']) == []


def test_restart_preserves_next_slot_and_exactly_one_claim(store):
    p = plan(store)
    reloaded = ScheduleStore(store.state_dir)
    assert reloaded.get(p['id'])['next_run_at'] == DUE
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: ScheduleStore(store.state_dir).enqueue_due(DUE), range(8)))
        claims = list(pool.map(lambda _: ScheduleStore(store.state_dir).claim(DUE), range(8)))
    assert len(store.runs(p['id'])) == 1
    assert len([r for r in claims if r]) == 1


def test_sleep_coalesces_multiple_missed_slots_into_latest(store):
    p = plan(store)
    now = DUE + 3 * 86400 + 3600
    assert store.enqueue_due(now) == 1
    assert store.runs(p['id'])[0]['due_at'] == DUE + 3 * 86400
    assert store.get(p['id'])['next_run_at'] == DUE + 4 * 86400


def test_skip_policy_records_why_no_work_ran(store):
    p = plan(store, missed_policy='skip')
    store.enqueue_due(DUE + 3600)
    assert store.runs(p['id'])[0]['status'] == 'skipped'
    assert store.claim(DUE + 3600) is None


def test_no_overlap_and_no_loss_of_latest_pending_slot(store):
    p = plan(store)
    store.enqueue_due(DUE)
    run = store.claim(DUE)
    assert store.start(run['id'], 123, DUE)
    assert store.enqueue_due(DUE + 86400) == 0
    store.finish(run['id'], 'success', now=DUE + 86400 + 5)
    assert store.enqueue_due(DUE + 86400 + 6) == 1
    assert len(store.runs(p['id'])) == 2


def test_retries_are_bounded_and_attempt_history_is_retained(store):
    p = plan(store, max_retries=2)
    store.enqueue_due(DUE)
    now = DUE
    for attempt, delay in [(1, 60), (2, 300), (3, 0)]:
        run = store.claim(now)
        assert run['attempt'] == attempt
        assert store.start(run['id'], 123, now)
        store.finish(run['id'], 'failed', f'failure {attempt}', now=now)
        assert store.run(run['id'])['status'] == ('retry' if attempt < 3 else 'failed')
        now += delay
    assert store.claim(now + 3600) is None
    attempts = store.runs(p['id'])[0]['attempts']
    assert [a['error'] for a in attempts] == ['failure 1', 'failure 2', 'failure 3']


def test_pause_fences_already_claimed_worker_and_resume_ignores_pause_gap(store):
    p = plan(store)
    store.enqueue_due(DUE)
    run = store.claim(DUE)
    store.toggle(p['id'], False, DUE + 1)
    assert store.start(run['id'], 123, DUE + 2) is False
    assert store.run(run['id'])['status'] == 'skipped'
    store.toggle(p['id'], True, DUE + 3 * 86400 + 1)
    assert store.get(p['id'])['next_run_at'] == DUE + 4 * 86400


def test_edit_preserves_pause_and_cancels_old_slots(store):
    p = plan(store)
    store.enqueue_due(DUE)
    store.toggle(p['id'], False, DUE)
    store.save('research/radar', '0 9 * * *', timezone='Asia/Shanghai',
               schedule_id=p['id'], editing=True, now=DUE)
    assert not store.get(p['id'])['enabled']
    assert store.runs(p['id'])[0]['status'] == 'skipped'


def test_lost_worker_recovers_but_live_os_lock_prevents_duplicate(store):
    p = plan(store)
    store.enqueue_due(DUE)
    run = store.claim(DUE)
    store.start(run['id'], 123, DUE)
    with file_lock(run_lock(store, run['id'])):
        assert recover(store, DUE + 300) == 0
        assert store.run(run['id'])['status'] == 'running'
    assert recover(store, DUE + 300) == 1
    assert store.run(run['id'])['status'] == 'retry'
    assert store.runs(p['id'])[0]['attempts'][0]['status'] == 'interrupted'


def test_heartbeat_renewal_handles_long_jobs(store):
    p = plan(store)
    store.enqueue_due(DUE)
    run = store.claim(DUE)
    store.start(run['id'], 123, DUE)
    store.renew(run['id'], DUE + 3600)
    assert store.expired(DUE + 3610) == []


def test_delete_retains_history_and_refuses_live_job_deletion(store):
    p = plan(store)
    store.enqueue_due(DUE)
    run = store.claim(DUE)
    store.start(run['id'], 123, DUE)
    with pytest.raises(ValueError, match='正在'):
        store.delete_for_job('research/radar')
    store.finish(run['id'], 'success', now=DUE + 1)
    store.delete_for_job('research/radar')
    assert store.list() == []
    assert len(store.runs(p['id'])) == 1


def test_one_plan_per_canonical_job(store):
    plan(store)
    with pytest.raises(ScheduleConflict):
        plan(store)


def test_concurrency_limit_applies_across_dispatchers(store):
    for i in range(4):
        store.save(f'job_{i}', '0 8 * * *', timezone='Asia/Shanghai', now=BASE)
    store.enqueue_due(DUE)
    with ThreadPoolExecutor(8) as pool:
        claims = list(pool.map(lambda _: ScheduleStore(store.state_dir).claim(DUE), range(8)))
    assert len([r for r in claims if r]) == 2


def test_timezone_schedule_is_independent_of_host_clock():
    before = datetime.fromisoformat('2026-09-25T00:00:00+00:00').timestamp()
    assert slot('0 9 * * *', before, 'Asia/Shanghai') == before + 3600
    assert slot('0 9 * * *', before, 'UTC') == before + 9 * 3600


@pytest.fixture
def runtime(tmp_path):
    for name in ('state', 'jobs', 'config', 'logs'):
        (tmp_path / name).mkdir(exist_ok=True)
    (tmp_path / 'jobs' / 'radar.yaml').write_text('name: Radar\nenabled: true\nprompt: test\n')
    return Runtime(str(tmp_path), *(str(tmp_path / name) for name in ('state', 'jobs', 'config', 'logs')))


def test_worker_executes_job_and_commits_actual_output(runtime, monkeypatch):
    storage = ScheduleStore(runtime.state_dir)
    p = storage.save('radar', '* * * * *', now=BASE)
    storage.enqueue_due(DUE)
    run = storage.claim(DUE)
    output = str(Path(runtime.workspace) / 'result.md')
    calls = []
    def execute(paths, name, cancel):
        calls.append(name)
        Path(output).write_text('verified')
        return 'success', '', output
    work(runtime, run['id'], execute=execute)
    assert calls == ['radar']
    assert storage.runs(p['id'])[0]['status'] == 'success'
    assert storage.runs(p['id'])[0]['output_path'] == output
    work(runtime, run['id'], execute=execute)
    assert calls == ['radar']


def test_disabled_job_is_recorded_without_running(runtime):
    storage = ScheduleStore(runtime.state_dir)
    p = storage.save('radar', '* * * * *', now=BASE)
    storage.enqueue_due(DUE)
    run = storage.claim(DUE)
    (Path(runtime.jobs_dir) / 'radar.yaml').write_text('name: Radar\nenabled: false\nprompt: test\n')
    work(runtime, run['id'], execute=lambda *_: pytest.fail('disabled job executed'))
    assert storage.runs(p['id'])[0]['status'] == 'blocked'


def test_dispatch_failure_is_recorded_and_retried(runtime):
    storage = ScheduleStore(runtime.state_dir)
    p = storage.save('radar', '* * * * *', now=BASE)
    def fail(*_):
        raise OSError('cannot spawn')
    assert dispatch_once(runtime, storage, now=DUE, spawn=fail) == 0
    assert storage.runs(p['id'])[0]['status'] == 'retry'
    assert storage.health(now=DUE)['online']


def test_budget_guard_prevents_model_call(runtime, monkeypatch):
    import threading
    from core import budget, state, lock, report_meta
    for module, names in [(state, ('_STATE_DIR', '_HISTORY_DIR')), (lock, ('_LOCK_DIR',)), (report_meta, ('META_FILE',))]:
        for name in names:
            monkeypatch.setattr(module, name, getattr(module, name))
    monkeypatch.setattr(budget, 'pipeline_allowed', lambda *_: (False, 'budget exhausted'))
    status, reason, output = execute_job(runtime, 'radar', threading.Event())
    assert (status, reason, output) == ('blocked', 'budget exhausted', None)
