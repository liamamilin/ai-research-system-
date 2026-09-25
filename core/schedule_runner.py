"""System-supervised dispatcher and isolated workers for every registered job."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from core.fileio import file_lock
from core.schedule_store import ScheduleStore

logger = logging.getLogger(__name__)
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'dispatch_schedules.py'


@dataclass(frozen=True)
class Runtime:
    workspace: str
    state_dir: str
    jobs_dir: str
    config_dir: str
    logs_dir: str

    @classmethod
    def from_settings(cls, settings):
        workspace = str(Path(settings.paths.config_dir).resolve().parent)
        return cls(workspace, *(str(Path(getattr(settings.paths, name)).resolve())
                               for name in ('state_dir', 'jobs_dir', 'config_dir', 'logs_dir')))

    def arguments(self) -> list[str]:
        args = []
        for name, value in vars(self).items():
            args.extend(['--' + name.replace('_', '-'), value])
        return args

    def identity(self) -> dict:
        return vars(self)


def canonical_job(runtime: Runtime, name: str) -> tuple[str, dict]:
    """Resolve aliases once and ensure the selected YAML stays inside jobs_dir."""
    from core.config import load_job
    job = load_job(runtime.jobs_dir, name)
    if not job:
        raise ValueError('研究任务不存在，请刷新任务列表')
    canonical = job['_file']
    root = Path(runtime.jobs_dir).resolve()
    paths = [root / (canonical + suffix) for suffix in ('.yaml', '.yml')]
    if not any(p.is_file() and p.resolve().is_relative_to(root) for p in paths):
        raise ValueError('任务路径超出 jobs 目录')
    if not job.get('enabled', True):
        raise ValueError('此 job 已停用，请先在任务配置中启用')
    if not str(job.get('prompt') or '').strip():
        raise ValueError('任务 prompt 为空，无法运行')
    return canonical, job


def run_lock(store: ScheduleStore, run_id: str) -> str:
    return str(Path(store.state_dir) / 'schedule-locks' / run_id)


def recover(store: ScheduleStore, now: float | None = None) -> int:
    """An expired lease alone is insufficient: a live worker's OS lock wins."""
    recovered = 0
    for run in store.expired(now):
        try:
            with file_lock(run_lock(store, run['id']), timeout=0):
                store.finish(run['id'], 'interrupted', '执行进程中断，已按重试策略处理', now=now)
                recovered += 1
        except TimeoutError:
            pass
    return recovered


def spawn_worker(runtime: Runtime, run: dict) -> None:
    """Spawn by argument vector, with no shell and no dependency on the Web PID."""
    log_dir = Path(runtime.logs_dir) / 'schedules'
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{run['id']}.log").open('ab') as log:
        process = subprocess.Popen([sys.executable, str(SCRIPT), *runtime.arguments(), '--worker', run['id']],
                                   cwd=runtime.workspace, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    # Reap finished children without blocking subsequent ticks.
    threading.Thread(target=process.wait, daemon=True).start()


def dispatch_once(runtime: Runtime, store: ScheduleStore, now: float | None = None, spawn=spawn_worker) -> int:
    """Recover dead claims, enqueue due slots, and dispatch at most two workers."""
    now = time.time() if now is None else now
    recover(store, now)
    store.enqueue_due(now)
    launched = 0
    for _ in range(2):
        run = store.claim(now)
        if not run:
            break
        try:
            spawn(runtime, run)
            launched += 1
        except Exception as exc:
            store.finish(run['id'], 'failed', f'启动执行进程失败：{exc}', now=now)
    store.heartbeat({'checked_at': now, 'status': 'ok', 'pid': os.getpid(), 'runtime': runtime.identity()})
    return launched


def execute_job(runtime: Runtime, job_name: str, cancel: threading.Event) -> tuple[str, str, str | None]:
    """Use the research engine's actual result, not run.py's historical exit code."""
    from core import state, lock, report_meta
    from core.budget import pipeline_allowed
    from core.config import load_system_config
    from core.engine import ResearchEngine
    state.use_state_dir(runtime.state_dir)
    lock._LOCK_DIR = str(Path(runtime.state_dir) / 'locks')
    report_meta.META_FILE = str(Path(runtime.state_dir) / 'report_meta.jsonl')
    try:
        canonical, job = canonical_job(runtime, job_name)
    except ValueError as exc:
        return 'blocked', str(exc), None
    allowed, reason = pipeline_allowed(load_system_config(runtime.config_dir))
    if not allowed:
        return 'blocked', reason, None
    # The engine shares its own per-job lock with CLI/Web manual runs.
    engine = ResearchEngine(config_dir=runtime.config_dir, jobs_dir=runtime.jobs_dir,
                            workspace_dir=runtime.workspace, cancel_token=cancel, user='scheduler')
    output = engine.run_job(canonical)
    if output and Path(output).is_file():
        return 'success', '', str(Path(output).resolve())
    last = state.StateManager.get(canonical) or {}
    return 'failed', ('任务超过运行时限' if cancel.is_set() else last.get('last_error') or '任务未生成报告，可能已有同名任务运行'), None


def work(runtime: Runtime, run_id: str, execute=execute_job) -> None:
    """Run one claimed slot under OS locks, with a lease and a bounded lifetime."""
    store = ScheduleStore(runtime.state_dir)
    run = store.run(run_id)
    if not run:
        return
    job_key = hashlib.sha256(run['job_name'].encode()).hexdigest()
    cancel, done = threading.Event(), threading.Event()
    try:
        with file_lock(run_lock(store, run_id), timeout=0):
            try:
                with file_lock(str(Path(runtime.state_dir) / 'schedule-job-locks' / job_key), timeout=0):
                    if not store.start(run_id, os.getpid()):
                        return
                    try:
                        _, job = canonical_job(runtime, run['job_name'])
                        config = job.get('runtime') or {}
                        timeout = min(max(int(config.get('timeout_seconds') or 3600), 60), 86400)
                    except (ValueError, TypeError, AttributeError) as exc:
                        store.finish(run_id, 'blocked', str(exc))
                        return
                    def heartbeat():
                        while not done.wait(10):
                            try:
                                store.renew(run_id)
                            except Exception:
                                logger.exception('Worker heartbeat failed')
                    def deadline():
                        if not done.wait(timeout):
                            cancel.set()
                            if not done.wait(60):
                                # Release OS locks by exiting; the dispatcher detects the lost lease.
                                os._exit(124)
                    threading.Thread(target=heartbeat, daemon=True).start()
                    threading.Thread(target=deadline, daemon=True).start()
                    if threading.current_thread() is threading.main_thread():
                        signal.signal(signal.SIGTERM, lambda *_: cancel.set())
                    try:
                        status, error, output = execute(runtime, run['job_name'], cancel)
                        store.finish(run_id, status, error, output)
                    except Exception as exc:
                        logger.exception('Scheduled job failed')
                        store.finish(run_id, 'failed', str(exc))
                    finally:
                        done.set()
            except TimeoutError:
                store.finish(run_id, 'failed', '同一 job 已有执行进程，稍后重试')
    except TimeoutError:
        # A duplicate worker for the same run must not modify its owner's claim.
        return


def serve(runtime: Runtime, once: bool = False) -> None:
    """Keep dispatching independently of the Web service; launchd supervises us."""
    os.chdir(runtime.workspace)
    store = ScheduleStore(runtime.state_dir)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        with file_lock(str(Path(runtime.state_dir) / 'schedule-dispatcher'), timeout=0):
            while not stop.is_set():
                try:
                    dispatch_once(runtime, store)
                except Exception as exc:
                    logger.exception('Dispatcher tick failed')
                    store.heartbeat({'checked_at': time.time(), 'status': 'error', 'error': str(exc),
                                     'pid': os.getpid(), 'runtime': runtime.identity()})
                if once:
                    return
                stop.wait(10)
            store.heartbeat({'checked_at': time.time(), 'status': 'stopped', 'runtime': runtime.identity()})
    except TimeoutError:
        logger.info('Another dispatcher already owns this state directory')
