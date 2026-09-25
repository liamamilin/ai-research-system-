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
