"""Install and verify the per-workspace macOS dispatcher, without touching cron."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

from core.fileio import file_lock
from core.schedule_runner import Runtime, SCRIPT
from core.schedule_store import ScheduleStore


def label(runtime: Runtime) -> str:
    return 'com.arec.scheduler.' + hashlib.sha256(runtime.state_dir.encode()).hexdigest()[:12]


def configuration(runtime: Runtime) -> dict:
    """Absolute paths and argument arrays support spaces and non-ASCII job names."""
    return {
        'Label': label(runtime),
        'ProgramArguments': [sys.executable, str(SCRIPT), *runtime.arguments()],
        'WorkingDirectory': runtime.workspace,
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10,
        'ProcessType': 'Background', 'AbandonProcessGroup': True,
        'StandardOutPath': str(Path(runtime.logs_dir) / 'scheduler-host.log'),
        'StandardErrorPath': str(Path(runtime.logs_dir) / 'scheduler-host.err.log'),
    }


def status(runtime: Runtime) -> dict:
    """A fresh heartbeat from the matching runtime proves the dispatcher is active."""
    health = ScheduleStore(runtime.state_dir).health()
    if health.get('runtime') != runtime.identity():
        health.update(online=False, detail='执行器未启动，或运行目录与当前项目不一致')
    return {**health, 'label': label(runtime), 'backend': 'launchd' if sys.platform == 'darwin' else 'external'}


def _launchctl(*args):
    result = subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f"系统调度器操作失败：{result.stderr.strip() or result.stdout.strip()}")
    return result


def install(runtime: Runtime, wait_seconds: float = 15) -> dict:
    """Install once, then require real dispatch heartbeats before reporting success."""
    if status(runtime)['online']:
        return status(runtime)
    if sys.platform != 'darwin':
        raise RuntimeError('执行器离线；请先使用系统服务运行 scripts/dispatch_schedules.py，再保存计划')
    with file_lock(str(Path(runtime.state_dir) / 'schedule-host-install'), timeout=20):
        if status(runtime)['online']:
            return status(runtime)
        agent_dir = Path.home() / 'Library' / 'LaunchAgents'
        agent_dir.mkdir(parents=True, exist_ok=True)
        Path(runtime.logs_dir).mkdir(parents=True, exist_ok=True)
        path = agent_dir / f'{label(runtime)}.plist'
        expected = configuration(runtime)
        previous = path.read_bytes() if path.exists() else None
        loaded = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{label(runtime)}'],
                                capture_output=True, text=True, timeout=15).returncode == 0
        same = previous is not None and plistlib.loads(previous) == expected
        if not same or not loaded:
            temporary = path.with_suffix('.plist.tmp')
            temporary.write_bytes(plistlib.dumps(expected))
            if loaded:
                _launchctl('bootout', f'gui/{os.getuid()}/{label(runtime)}')
            temporary.replace(path)
            try:
                _launchctl('bootstrap', f'gui/{os.getuid()}', str(path))
            except Exception:
                if previous is not None:
                    path.write_bytes(previous)
                    if loaded:
                        _launchctl('bootstrap', f'gui/{os.getuid()}', str(path))
                else:
                    path.unlink(missing_ok=True)
                raise
        else:
            _launchctl('kickstart', f'gui/{os.getuid()}/{label(runtime)}')
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            health = status(runtime)
            if health['online']:
                return health
            time.sleep(0.25)
        raise RuntimeError('系统任务已安装，但尚未收到执行器心跳，请查看 logs/scheduler-host.err.log')
