"""Product API contracts for creating any job with a durable recurring plan."""
import time

import pytest

from core.schedule_store import ScheduleStore
from web.services import managed_scheduler as managed


@pytest.fixture
def admin(client, web_env, monkeypatch):
    root, _ = web_env
    calls = []
    def online():
        calls.append(True)
        managed.store().heartbeat({'checked_at': time.time(), 'status': 'ok',
                                  'runtime': managed.runtime().identity()})
        return {'online': True}
    monkeypatch.setattr(managed, 'ensure_online', online)
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123'})
    return client, root, {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}, calls


def create(admin, **extra):
    client, _, headers, _ = admin
    return client.post('/api/jobs', headers=headers, json={
        'name': '新增 job', 'category': 'research', 'description': '含 "引号" 的任务',
        'prompt': '第一行\n第二行', 'output': 'output/recurring_{datetime}.md',
        'recurrence': {'schedule': '0 8 * * *', 'timezone': 'Asia/Shanghai'}, **extra})


def test_new_job_and_plan_create_atomically_without_touching_cron(admin, monkeypatch):
    from web.services import scheduler
    monkeypatch.setattr(scheduler, '_set_crontab', lambda *_: pytest.fail('new plan wrote cron'))
    client, root, headers, calls = admin
    response = create(admin)
    assert response.status_code == 201, response.text
    storage = ScheduleStore(str(root / 'state'))
    plan = storage.list()[0]
    assert plan['job_name'] == 'research/新增_job'
    assert plan['enabled'] and plan['max_retries'] == 2 and calls
    assert storage.runs(plan['id']) == []  # Registration never runs a paid job immediately.
    listing = client.get('/api/scheduler').json()
    item = next(j for j in listing['jobs'] if j['id'] == plan['id'])
    assert item['backend'] == 'managed' and len(item['next_runs']) == 3
    assert listing['dispatcher']['online'] and listing['health']['status'] == 'ok'
    job = client.get('/api/jobs/research/新增_job').json()
    assert job['prompt'] == '第一行\n第二行'
    assert client.put(f"/api/scheduler/{plan['id']}/toggle", headers=headers, json={'enabled': False}).status_code == 200
    assert client.put(f"/api/scheduler/{plan['id']}", headers=headers, json={'schedule': '0 9 * * *'}).json()['enabled'] is False
    assert client.delete('/api/jobs/research/新增_job', headers=headers).status_code == 200
    assert storage.list() == []


def test_alias_cannot_create_duplicate_plan(admin):
    client, _, headers, _ = admin
    assert create(admin).status_code == 201
    response = client.post('/api/scheduler', headers=headers, json={
        'job_name': '新增_job', 'schedule': '0 12 * * *'})
    assert response.status_code == 409
    assert len(managed.store().list()) == 1


@pytest.mark.parametrize('extra', [
    {'schedule': '0 25 * * *'}, {'schedule': '0 8 * * *', 'timezone': 'Nowhere/Invalid'},
    {'schedule': '0 8 * * *', 'max_retries': 6}, {'schedule': '0 8 * * *', 'max_retries': True},
    {'schedule': '0 8 * * *', 'missed_policy': 'all'},
])
def test_invalid_recurrence_does_not_create_partial_job(admin, extra):
    _, root, _, calls = admin
    assert create(admin, recurrence=extra).status_code == 422
    assert not list((root / 'jobs').rglob('*.yaml')) and not calls


def test_host_offline_is_not_reported_as_success(admin, monkeypatch):
    _, root, _, _ = admin
    def offline():
        raise RuntimeError('no dispatcher heartbeat')
    monkeypatch.setattr(managed, 'ensure_online', offline)
    response = create(admin)
    assert response.status_code == 503 and 'heartbeat' in response.text
    assert not list((root / 'jobs').rglob('*.yaml')) and not managed.store().list()


def test_plan_persistence_failure_rolls_back_new_job(admin, monkeypatch):
    _, root, _, _ = admin
    def fail(*_, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(managed, 'save', fail)
    assert create(admin).status_code == 503
    assert not list((root / 'jobs').rglob('*.yaml'))


def test_editor_cannot_create_recurring_job(client, web_env, monkeypatch):
    monkeypatch.setattr(managed, 'ensure_online', lambda: pytest.fail('editor started dispatcher'))
    client.post('/api/auth/login', json={'username': 'editor', 'password': 'editor-pass-123'})
    result = client.post('/api/jobs', headers={'X-CSRF-Token': client.cookies.get('ai_research_csrf')},
                         json={'name': 'x', 'recurrence': {'schedule': '* * * * *'}})
    assert result.status_code == 403


def test_missing_disabled_and_legacy_id_collision_rejected(admin):
    client, root, headers, _ = admin
    (root / 'jobs/off.yaml').write_text('enabled: false\nprompt: test\n')
    for name in ('missing', 'off', '../escape'):
        assert client.post('/api/scheduler', headers=headers, json={'job_name': name, 'schedule': '* * * * *'}).status_code == 422
    create(admin, recurrence=None)
    assert client.post('/api/scheduler', headers=headers, json={
        'id': 'test_job', 'job_name': '新增_job', 'schedule': '* * * * *'}).status_code == 409


def test_execution_history_timezone_and_delete_while_running(admin):
    client, root, headers, _ = admin
    create(admin)
    storage = managed.store()
    plan = storage.list()[0]
    storage.enqueue_due(plan['next_run_at'])
    run = storage.claim(plan['next_run_at'])
    storage.start(run['id'], 123, plan['next_run_at'])
    assert client.delete('/api/jobs/research/新增_job', headers=headers).status_code == 409
    assert (root / 'jobs/research/新增_job.yaml').exists()
    history = client.get(f"/api/scheduler/{plan['id']}/runs").json()['runs'][0]
    assert history['due_at_iso'].endswith('+08:00') and history['started_at_iso']
    storage.finish(run['id'], 'success')
    assert client.delete(f"/api/scheduler/{plan['id']}", headers=headers).status_code == 200
    assert client.get(f"/api/scheduler/{plan['id']}/runs").json()['runs'][0]['status'] == 'success'


def test_concurrent_creates_cannot_clobber_each_other(admin, monkeypatch):
    """Two creates of the same name must not leave a plan without its job file.

    The old path checked ``isfile()`` and then renamed, so both requests passed
    the check; the one that lost the plan's unique index removed the file the
    winner had just created, and the plan pointed at nothing.
    """
    import os
    import threading
    import time

    from web.services import yaml_io

    client, root, headers, _ = admin
    start = threading.Barrier(2)
    results: list[int] = []
    lock = threading.Lock()

    real_create = yaml_io.atomic_create

    def slow_create(path, content):
        # Widen the window so both requests reach the exclusive publish together.
        start.wait(timeout=5)
        time.sleep(0.2)
        return real_create(path, content)

    monkeypatch.setattr(yaml_io, 'atomic_create', slow_create)

    def attempt():
        start.wait(timeout=5)
        response = client.post('/api/jobs', headers=headers, json={
            'name': '并发 job', 'category': 'research', 'description': '竞态',
            'prompt': '正文', 'output': 'output/race_{datetime}.md',
            'recurrence': {'schedule': '0 8 * * *', 'timezone': 'Asia/Shanghai'}})
        with lock:
            results.append(response.status_code)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert sorted(results) == [201, 409]
    # The endpoint normalises the file name, so assert on what it wrote.
    job_file = os.path.join(root, 'jobs', 'research', '并发_job.yaml')
    assert os.path.isfile(job_file), "the winning job file was deleted by the loser"
    plans = ScheduleStore(managed.store().state_dir).list()
    assert [p['job_name'] for p in plans] == ['research/并发_job']


def test_a_failed_plan_leaves_no_job_file_behind(admin, monkeypatch):
    """The documented all-or-nothing rollback, checked on disk."""
    import os

    client, root, headers, _ = admin

    def refuse(*_args, **_kwargs):
        raise RuntimeError('计划写入失败')

    monkeypatch.setattr(managed, 'save', refuse)
    # _mutate turns any persistence failure into 503, not a bare 500.
    assert create(admin).status_code == 503
    assert not os.path.exists(os.path.join(root, 'jobs', 'research', '新增_job.yaml'))
    assert ScheduleStore(managed.store().state_dir).list() == []


def test_a_rollback_never_deletes_a_file_it_did_not_create(admin, monkeypatch):
    """A losing request must leave the winner's file untouched."""
    import os

    client, root, headers, _ = admin
    job_file = os.path.join(root, 'jobs', 'research', '新增_job.yaml')
    os.makedirs(os.path.dirname(job_file), exist_ok=True)
    with open(job_file, 'w', encoding='utf-8') as fh:
        fh.write('name: 别人创建的\nprompt: 保留我\n')

    def refuse(*_args, **_kwargs):
        raise RuntimeError('计划写入失败')

    monkeypatch.setattr(managed, 'save', refuse)
    create(admin)
    assert os.path.isfile(job_file)
    with open(job_file, encoding='utf-8') as fh:
        assert '保留我' in fh.read()


def test_a_new_plan_is_not_reported_as_verified(admin):
    """Saving a plan proves the dispatcher, never the model: no green yet."""
    client, _, headers, _ = admin
    assert create(admin).status_code == 201

    listing = client.get('/api/scheduler', headers=headers).json()
    managed_job = next(j for j in listing['jobs'] if j['backend'] == 'managed')
    state = next(s for s in listing['health']['jobs'] if s['id'] == managed_job['id'])
    assert state['status'] == 'unverified'
    assert '尚未执行过' in state['detail']
