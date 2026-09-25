#!/usr/bin/env python3
"""Real launchd acceptance test in an isolated workspace, with a local fake LLM.

Creates one temporary agent and job, observes two natural minute boundaries,
misses a boundary with the dispatcher stopped, and verifies one catch-up run.
No production plans or paid model calls are used. The temporary agent is removed.
"""
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.schedule_host import configuration, install, label
from core.schedule_runner import Runtime
from core.schedule_store import ScheduleStore
import yaml


def main():
    workspace = Path(tempfile.mkdtemp(prefix='arec-scheduler-验收 ', dir='/private/tmp'))
    for name in ('config', 'jobs/research', 'state', 'logs', 'output'):
        (workspace / name).mkdir(parents=True)
    requests = []
    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert self.path == '/v1/chat/completions' and not body.get('stream')
            requests.append(time.time())
            payload = {'id': 'acceptance', 'object': 'chat.completion', 'created': int(time.time()),
                       'model': 'local-scheduler-test', 'choices': [{'index': 0, 'finish_reason': 'stop',
                       'message': {'role': 'assistant', 'content': '# 周期调度验收\n\n这是系统自动触发并由研究引擎写出的报告。\n'}}],
                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            data = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = {'ai': {'model': 'local-scheduler-test', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
                     'api_key_env': '', 'stream': False, 'max_retries': 0, 'timeout': 30},
              'research': {'mode': 'agent', 'max_rounds': 1}, 'notifications': {'enabled': False},
              'defaults': {'output_dir': 'output'}}
    (workspace / 'config/system.yaml').write_text(yaml.safe_dump(config))
    name = 'research/新增任务_自动运行'
    (workspace / f'jobs/{name}.yaml').write_text(yaml.safe_dump({
        'name': '新增任务 自动运行', 'enabled': True, 'prompt': 'Write a brief scheduler verification report.',
        'runtime': {'timeout_seconds': 60}, 'output': 'output/probe_{datetime}.md'}, allow_unicode=True))
    runtime = Runtime(str(workspace), *(str(workspace / name) for name in ('state', 'jobs', 'config', 'logs')))
    storage = ScheduleStore(runtime.state_dir)
    plan = storage.save(name, '* * * * *', max_retries=0)
    agent = label(runtime)
    address = f'gui/{os.getuid()}/{agent}'
    plist = Path.home() / 'Library/LaunchAgents' / f'{agent}.plist'
    print(f'Acceptance workspace: {workspace}', flush=True)
    def wait_until(condition, timeout, description):
        deadline = time.monotonic() + timeout
        next_message = 0
        while time.monotonic() < deadline:
            result = condition()
            if result:
                return result
            bad = [r for r in storage.runs(plan['id']) if r['status'] in ('failed', 'blocked', 'interrupted')]
            if bad:
                raise RuntimeError(f'Execution failed: {bad}')
            if time.monotonic() >= next_message:
                print(f'Waiting: {description}', flush=True)
                next_message = time.monotonic() + 20
            time.sleep(1)
        raise TimeoutError(description)
    def successes():
        return [r for r in storage.runs(plan['id']) if r['status'] == 'success']
    proof = {'workspace': str(workspace), 'job_name': name, 'started_at': datetime.now().astimezone().isoformat()}
    try:
        install(runtime)
        wait_until(lambda: len(successes()) >= 2, 155, 'two natural scheduled executions')
        first_two = successes()
        assert len(first_two) == 2 and len(requests) == 2
        assert abs(first_two[0]['due_at'] - first_two[1]['due_at']) == 60
        print('PASS: two automatic report generations at consecutive minute slots', flush=True)
        subprocess.run(['/bin/launchctl', 'bootout', address], check=True)
        due = storage.get(plan['id'])['next_run_at']
        wait_until(lambda: time.time() >= due + 8, 80, 'miss one slot while dispatcher is offline')
        assert len(successes()) == 2
        subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], check=True)
        wait_until(lambda: len(successes()) >= 3, 35, 'automatic catch-up after dispatcher restart')
        latest = successes()[0]
        assert latest['due_at'] == due and latest['started_at'] > due + 5
        assert len(requests) == 3
        print('PASS: exactly one missed slot caught up after restart', flush=True)
        storage.toggle(plan['id'], False)
        original_pid = storage.health()['pid']
        os.kill(original_pid, signal.SIGTERM)
        wait_until(lambda: storage.health().get('online') and storage.health().get('pid') != original_pid,
                   35, 'launchd restarts a stopped dispatcher')
        print('PASS: launchd restarted dispatcher; saved paused plan survived', flush=True)
        assert not storage.get(plan['id'])['enabled']
        proof.update(result='passed', model_calls=len(requests), runs=successes(),
                     checks=['two_natural_slots', 'one_offline_catchup', 'supervisor_restart', 'paused_plan_persisted', 'no_web_server'],
                     finished_at=datetime.now().astimezone().isoformat())
        for run in successes():
            assert Path(run['output_path']).is_file()
            assert '周期调度验收' in Path(run['output_path']).read_text()
        (workspace / 'acceptance.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2))
        print(f'PROOF: {workspace / "acceptance.json"}', flush=True)
    finally:
        storage.toggle(plan['id'], False)
        subprocess.run(['/bin/launchctl', 'bootout', address], capture_output=True)
        if plist.exists():
            import plistlib
            if plistlib.loads(plist.read_bytes()) == configuration(runtime):
                plist.unlink()
        server.shutdown()
        print('Temporary launchd agent removed; reports retained only in acceptance workspace', flush=True)


if __name__ == '__main__':
    main()
