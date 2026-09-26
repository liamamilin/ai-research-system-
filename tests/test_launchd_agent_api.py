"""The schedule page can now edit, pause, delete and restore the matrix agents.

These exercise the HTTP contract with a throwaway agent directory, so the
machine's real launchd schedule is never touched.
"""

from __future__ import annotations

import plistlib

import pytest

from web.services import launchd_agents as la

DAILY = "com.arec.pipeline.daily"
CATCHUP = "com.arec.pipeline.catchup"


@pytest.fixture
def system_agents(tmp_path, monkeypatch, web_env):
    # Depends on web_env so it runs *after* conftest stubs
    # list_launchd_agents to []; this patch has to be the last word.
    directory = tmp_path / "LaunchAgents"
    directory.mkdir()
    for label in la.LABELS:
        config = {"Label": label,
                  "ProgramArguments": ["/usr/bin/python3", "/repo/scripts/ensure_round.py"],
                  "RunAtLoad": True}
        config["StartCalendarInterval" if la.KINDS[label] == "daily" else "StartInterval"] = (
            {"Hour": 6, "Minute": 0} if la.KINDS[label] == "daily" else 1800)
        (directory / f"{label}.plist").write_bytes(plistlib.dumps(config))

    loaded = set(la.LABELS)
    calls: list[tuple[str, ...]] = []

    class Result:
        def __init__(self, returncode, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def fake_run(cmd, **kwargs):
        args = list(cmd[1:]) if cmd and str(cmd[0]).endswith("launchctl") else list(cmd)
        if args and args[0] == "print":
            label = args[-1].split("/")[-1]
            return Result(0 if label in loaded else 113, "" if label in loaded else "not found")
        calls.append(tuple(args))
        if args and args[0] == "bootstrap":
            from pathlib import Path
            loaded.add(Path(args[-1]).stem)
        elif args and args[0] == "bootout":
            loaded.discard(args[-1].split("/")[-1])
        return Result(0, "")

    monkeypatch.setattr(la.subprocess, "run", fake_run)
    monkeypatch.setattr(la.sys, "platform", "darwin")
    # conftest points agent_dir at an empty dir; override it with ours. The real
    # list_launchd_agents then finds these plists, so nothing needs stubbing.
    monkeypatch.setattr(la, "agent_dir", lambda: directory)
    from web.services import scheduler as scheduler_service
    monkeypatch.setattr(scheduler_service.subprocess, "run", fake_run)

    class Env:
        path = directory
        loaded_labels = loaded

    return Env


@pytest.fixture
def admin(client, web_env):
    client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin-pass-123'})
    return client, web_env[0], {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}


def _agent(client, label):
    listing = client.get('/api/scheduler').json()
    return next(j for j in listing['jobs'] if j['id'] == label)


def test_matrix_agents_are_listed_as_editable_with_their_schedule(admin, system_agents):
    client, _, _ = admin
    daily = _agent(client, DAILY)
    assert daily['backend'] == 'launchd' and daily['editable'] is True
    assert daily['schedule'] == '0 6 * * *' and daily['enabled'] is True
    assert _agent(client, CATCHUP)['schedule'] == '*/30 * * * *'


def test_editing_the_daily_time_through_the_api(admin, system_agents):
    client, _, headers = admin
    response = client.put(f'/api/scheduler/{DAILY}', headers=headers, json={'schedule': '45 20 * * *'})
    assert response.status_code == 200, response.text
    assert response.json()['schedule'] == '45 20 * * *'
    assert _agent(client, DAILY)['schedule'] == '45 20 * * *'
    assert la.read_config(DAILY, system_agents.path)['StartCalendarInterval'] == {'Hour': 20, 'Minute': 45}


def test_editing_the_catchup_interval_through_the_api(admin, system_agents):
    client, _, headers = admin
    response = client.put(f'/api/scheduler/{CATCHUP}', headers=headers, json={'schedule': '*/15 * * * *'})
    assert response.status_code == 200, response.text
    assert la.read_config(CATCHUP, system_agents.path)['StartInterval'] == 900


def test_an_unusable_schedule_is_rejected_and_changes_nothing(admin, system_agents):
    client, _, headers = admin
    before = (system_agents.path / f'{DAILY}.plist').read_bytes()
    # A cron shape the daily agent cannot express must not reach the plist.
    assert client.put(f'/api/scheduler/{DAILY}', headers=headers,
                      json={'schedule': '0 6 * * 1'}).status_code == 422
    assert client.put(f'/api/scheduler/{CATCHUP}', headers=headers,
                      json={'schedule': '0 6 * * *'}).status_code == 422
    assert (system_agents.path / f'{DAILY}.plist').read_bytes() == before


def test_pausing_unloads_the_agent_but_keeps_its_schedule(admin, system_agents):
    client, _, headers = admin
    assert client.put(f'/api/scheduler/{DAILY}/toggle', headers=headers,
                      json={'enabled': False}).status_code == 200
    assert DAILY not in system_agents.loaded_labels
    assert (system_agents.path / f'{DAILY}.plist').exists()
    daily = _agent(client, DAILY)
    assert daily['enabled'] is False and daily['schedule'] == '0 6 * * *'
    assert client.put(f'/api/scheduler/{DAILY}/toggle', headers=headers,
                      json={'enabled': True}).status_code == 200
    assert DAILY in system_agents.loaded_labels


def test_deleting_is_recoverable_through_the_api(admin, system_agents, web_env):
    client, root, headers = admin
    response = client.delete(f'/api/scheduler/{DAILY}', headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()['restorable'] is True
    assert not (system_agents.path / f'{DAILY}.plist').exists()
    assert DAILY not in system_agents.loaded_labels
    assert all(j['id'] != DAILY for j in client.get('/api/scheduler').json()['jobs'])

    listing = client.get('/api/scheduler').json()
    removed = listing['removed_system_jobs']
    assert [entry['label'] for entry in removed] == [DAILY]
    assert removed[0]['schedule'] == '0 6 * * *'
    # The other agent is untouched.
    assert _agent(client, CATCHUP)['enabled'] is True

    restored = client.post(f'/api/scheduler/{DAILY}/restore', headers=headers)
    assert restored.status_code == 200, restored.text
    assert (system_agents.path / f'{DAILY}.plist').exists()
    assert DAILY in system_agents.loaded_labels
    assert _agent(client, DAILY)['schedule'] == '0 6 * * *'
    assert client.get('/api/scheduler').json()['removed_system_jobs'] == []


def test_restoring_something_that_was_never_deleted_is_a_clear_error(admin, system_agents):
    client, _, headers = admin
    response = client.post(f'/api/scheduler/{CATCHUP}/restore', headers=headers)
    assert response.status_code == 503
    assert '没有找到' in response.json()['error']['message']


def test_restore_is_refused_for_an_ordinary_plan(admin, system_agents):
    client, _, headers = admin
    assert client.post('/api/scheduler/some_cron_job/restore', headers=headers).status_code == 400


def test_editing_requires_admin(admin, system_agents, client, web_env):
    _, root, _ = admin
    client.post('/api/auth/logout')
    client.post('/api/auth/login', json={'username': 'viewer', 'password': 'viewer-pass-123'})
    headers = {'X-CSRF-Token': client.cookies.get('ai_research_csrf')}
    assert client.put(f'/api/scheduler/{DAILY}', headers=headers,
                      json={'schedule': '0 9 * * *'}).status_code == 403
    assert client.delete(f'/api/scheduler/{DAILY}', headers=headers).status_code == 403


def test_editing_needs_the_csrf_token(admin, system_agents):
    client, _, _ = admin
    assert client.put(f'/api/scheduler/{DAILY}', json={'schedule': '0 9 * * *'}).status_code == 403


def test_editing_an_uninstalled_agent_points_at_the_install_script(admin, system_agents):
    client, _, headers = admin
    (system_agents.path / f'{DAILY}.plist').unlink()
    response = client.put(f'/api/scheduler/{DAILY}', headers=headers, json={'schedule': '0 9 * * *'})
    assert response.status_code == 409
    assert 'install_launchd.sh' in response.json()['error']['message']


def test_managed_plans_still_work_alongside_the_system_agents(admin, system_agents, monkeypatch):
    from web.services import managed_scheduler as managed
    import time
    monkeypatch.setattr(managed, 'ensure_online', lambda: managed.store().heartbeat(
        {'checked_at': time.time(), 'status': 'ok', 'runtime': managed.runtime().identity()}))
    client, root, headers = admin
    (root / 'jobs' / 'monitoring').mkdir(parents=True, exist_ok=True)
    (root / 'jobs' / 'monitoring' / 'ai_monitoring.yaml').write_text(
        'name: "AI 监控"\ndescription: "d"\nenabled: true\nprompt: "p"\n'
        'output: "output/m_{date}.md"\n', encoding='utf-8')
    created = client.post('/api/scheduler', headers=headers,
                          json={'job_name': 'monitoring/ai_monitoring', 'schedule': '30 7 * * *'})
    assert created.status_code == 200, created.text
    plan_id = created.json()['id']
    assert client.put(f'/api/scheduler/{plan_id}', headers=headers,
                      json={'schedule': '45 8 * * *'}).status_code == 200
    assert client.delete(f'/api/scheduler/{plan_id}', headers=headers).status_code == 200
    # System agents survive an unrelated plan being removed.
    assert _agent(client, DAILY)['editable'] is True
