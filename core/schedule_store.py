"""Durable recurring plans and execution slots shared by web and dispatcher."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import sqlite3
import time
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core import cron

ACTIVE = ('queued', 'starting', 'running', 'retry')
LEASE_SECONDS = 120


class ScheduleConflict(ValueError):
    """A plan already exists for this job or ID."""


def zone(name: str):
    """Resolve an IANA timezone or the machine's local timezone rules."""
    try:
        return cron.local_now().tzinfo if name == 'local' else ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError('时区无效，请使用 Asia/Shanghai 等 IANA 时区') from exc


def slot(expression: str, at: float, timezone: str, backwards: bool = False) -> float:
    """Find the next (or latest) matching slot using timezone-aware calendar rules."""
    slots = cron.fire_times(cron.parse_schedule(expression), datetime.fromtimestamp(at, zone(timezone)),
                            count=1, backwards=backwards)
    if not slots:
        raise ValueError('未来或过去 8 年没有匹配日期，请检查执行时间')
    return slots[0].timestamp()


def iso(at: float | None, timezone: str = 'local') -> str | None:
    return datetime.fromtimestamp(at, zone(timezone)).isoformat(timespec='seconds') if at else None


class ScheduleStore:
    """SQLite transactions make slot creation and claims safe across processes."""

    def __init__(self, state_dir: str):
        self.state_dir = str(Path(state_dir).resolve())
        Path(self.state_dir).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(self.state_dir) / 'schedules.db')
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY, job_name TEXT NOT NULL,
                    schedule TEXT NOT NULL, timezone TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, missed_policy TEXT NOT NULL,
                    max_retries INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                    next_run_at REAL NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS schedule_job ON schedules(job_name) WHERE deleted=0;
                CREATE TABLE IF NOT EXISTS schedule_runs (
                    id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, job_name TEXT NOT NULL,
                    version INTEGER NOT NULL, due_at REAL NOT NULL, status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL,
                    ready_at REAL NOT NULL, lease_until REAL, pid INTEGER,
                    started_at REAL, finished_at REAL, error TEXT NOT NULL DEFAULT '',
                    output_path TEXT, created_at REAL NOT NULL, timezone TEXT NOT NULL DEFAULT '',
                    UNIQUE(schedule_id, version, due_at)
                );
                CREATE INDEX IF NOT EXISTS run_status ON schedule_runs(status, ready_at);
                CREATE INDEX IF NOT EXISTS run_schedule ON schedule_runs(schedule_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS schedule_attempts (
                    run_id TEXT NOT NULL, attempt INTEGER NOT NULL, started_at REAL,
                    finished_at REAL, status TEXT NOT NULL, error TEXT, output_path TEXT,
                    PRIMARY KEY(run_id,attempt)
                );
                CREATE TABLE IF NOT EXISTS scheduler_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
        self._migrate_runs_timezone()

    def _migrate_runs_timezone(self):
        """Stamp each run with the plan timezone it was scheduled in.

        History outlives its plan, and the API renders a stored epoch in a
        timezone. Without this column a deleted plan's runs fall back to the
        server's local zone, silently reinterpreting "03:00 in Asia/Shanghai" as
        a different moment. Old rows are backfilled from the plan they belong
        to, so existing history keeps its original zone.
        """
        with self.connect() as db:
            columns = {row["name"] for row in db.execute("PRAGMA table_info(schedule_runs)")}
            if "timezone" in columns:
                return
            db.execute("ALTER TABLE schedule_runs ADD COLUMN timezone TEXT NOT NULL DEFAULT ''")
            for plan in db.execute("SELECT id, timezone FROM schedules").fetchall():
                db.execute("UPDATE schedule_runs SET timezone=? WHERE schedule_id=? AND timezone=''",
                           (plan["timezone"], plan["id"]))

    @contextmanager
    def connect(self, write: bool = False):
        """Open a bounded SQLite transaction; writers serialize before reading."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        try:
            db.execute('PRAGMA journal_mode=WAL')
        except sqlite3.OperationalError:
            # Switching the journal mode needs a brief exclusive lock, and two
            # connections doing it at once makes the loser fail immediately
            # instead of waiting on the busy handler. The mode is a property of
            # the file, not of the connection, so the next connect() sees it.
            pass
        db.execute('PRAGMA synchronous=FULL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, schedule_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM schedules WHERE id=? AND deleted=0', (schedule_id,)).fetchone()
            return dict(row) if row else None

    def list(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM schedules WHERE deleted=0 ORDER BY created_at')]

    def save(self, job_name: str, expression: str, *, schedule_id: str | None = None,
             timezone: str = 'local', missed_policy: str = 'latest', max_retries: int = 2,
             editing: bool = False, now: float | None = None) -> dict:
        """Create or edit a plan; edits preserve enabled state and cancel pending old slots."""
        now = time.time() if now is None else now
        schedule_id = schedule_id or uuid.uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}', schedule_id):
            raise ValueError('调度 ID 仅支持字母、数字、下划线、点和短横线，最长 128 字符')
        if missed_policy not in ('latest', 'skip'):
            raise ValueError('补跑策略必须是 latest 或 skip')
        if type(max_retries) is not int or not 0 <= max_retries <= 5:
            raise ValueError('失败重试次数需在 0–5 之间')
        expression = ' '.join(cron.parse_schedule(expression).values())
        next_run = slot(expression, now, timezone)
        try:
            with self.connect(True) as db:
                existing = db.execute('SELECT * FROM schedules WHERE id=? AND deleted=0', (schedule_id,)).fetchone()
                if editing and not existing:
                    raise KeyError(schedule_id)
                if existing and not editing:
                    raise ScheduleConflict('调度 ID 已存在')
                if existing:
                    if existing['job_name'] != job_name:
                        raise ValueError('编辑计划时不能替换研究任务，请另建计划')
                    db.execute('''UPDATE schedules SET schedule=?, timezone=?, missed_policy=?, max_retries=?,
                                  next_run_at=?, version=version+1, updated_at=? WHERE id=?''',
                               (expression, timezone, missed_policy, max_retries, next_run, now, schedule_id))
                    self._cancel_pending(db, schedule_id, now, '计划已修改，旧的待执行任务取消')
                else:
                    db.execute('''INSERT INTO schedules
                        (id,job_name,schedule,timezone,missed_policy,max_retries,next_run_at,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?)''',
                               (schedule_id, job_name, expression, timezone, missed_policy, max_retries, next_run, now, now))
        except sqlite3.IntegrityError as exc:
            raise ScheduleConflict('这个 job 已有周期计划，请编辑现有计划') from exc
        return self.get(schedule_id)

    @staticmethod
    def _cancel_pending(db, schedule_id, now, reason):
        db.execute("""UPDATE schedule_runs SET status='skipped',finished_at=?,error=?
                      WHERE schedule_id=? AND status IN ('queued','retry','starting')""", (now, reason, schedule_id))

    def toggle(self, schedule_id: str, enabled: bool, now: float | None = None) -> bool:
        """Resume from the next future slot; paused intervals are never backfilled."""
        now = time.time() if now is None else now
        with self.connect(True) as db:
            row = db.execute('SELECT * FROM schedules WHERE id=? AND deleted=0', (schedule_id,)).fetchone()
            if not row:
                return False
            if bool(row['enabled']) == enabled:
                return True
            next_run = slot(row['schedule'], now, row['timezone'])
            db.execute('UPDATE schedules SET enabled=?,version=version+1,next_run_at=?,updated_at=? WHERE id=?',
                       (int(enabled), next_run, now, schedule_id))
            self._cancel_pending(db, schedule_id, now, '计划已暂停或恢复，旧的待执行任务取消')
        return True

    def delete(self, schedule_id: str, now: float | None = None) -> bool:
        """Remove a plan but retain its execution history."""
        now = time.time() if now is None else now
        with self.connect(True) as db:
            changed = db.execute('UPDATE schedules SET deleted=1,enabled=0,version=version+1 WHERE id=? AND deleted=0', (schedule_id,)).rowcount
            self._cancel_pending(db, schedule_id, now, '计划已删除')
        return bool(changed)

    def runs(self, schedule_id: str, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            result = [dict(r) for r in db.execute('SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?',
                                                 (schedule_id, min(max(limit, 1), 100)))]
            for run in result:
                run['attempts'] = [dict(a) for a in db.execute('SELECT * FROM schedule_attempts WHERE run_id=? ORDER BY attempt', (run['id'],))]
            return result

    def delete_for_job(self, job_name: str) -> None:
        """Fence queued workers before removing a job file; refuse a live execution."""
        now = time.time()
        with self.connect(True) as db:
            if db.execute("SELECT 1 FROM schedule_runs WHERE job_name=? AND status='running'", (job_name,)).fetchone():
                raise ValueError('Job 正在由周期调度执行，暂时无法删除')
            for row in db.execute('SELECT id FROM schedules WHERE job_name=? AND deleted=0', (job_name,)).fetchall():
                db.execute('UPDATE schedules SET deleted=1,enabled=0,version=version+1 WHERE id=?', (row['id'],))
                self._cancel_pending(db, row['id'], now, '研究任务已删除')

    def run(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM schedule_runs WHERE id=?', (run_id,)).fetchone()
            return dict(row) if row else None

    def enqueue_due(self, now: float | None = None) -> int:
        """Coalesce overdue slots into one; never enqueue overlapping work for a job."""
        now = time.time() if now is None else now
        count = 0
        with self.connect(True) as db:
            for plan in db.execute('SELECT * FROM schedules WHERE enabled=1 AND deleted=0 AND next_run_at<=?', (now,)).fetchall():
                active = db.execute("SELECT 1 FROM schedule_runs WHERE job_name=? AND status IN ('queued','starting','running','retry')", (plan['job_name'],)).fetchone()
                if active:
                    continue
                due = max(plan['next_run_at'], slot(plan['schedule'], now, plan['timezone'], backwards=True))
                skipped = plan['missed_policy'] == 'skip' and now - due >= 60
                db.execute('''INSERT OR IGNORE INTO schedule_runs
                    (id,schedule_id,job_name,version,due_at,status,max_retries,ready_at,created_at,error,finished_at,timezone)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                           (uuid.uuid4().hex, plan['id'], plan['job_name'], plan['version'], due,
                            'skipped' if skipped else 'queued', plan['max_retries'], now, now,
                            '已按策略跳过错过的计划' if skipped else '', now if skipped else None,
                            plan['timezone']))
                db.execute('UPDATE schedules SET next_run_at=? WHERE id=?',
                           (slot(plan['schedule'], now, plan['timezone']), plan['id']))
                count += 1
        return count

    def claim(self, now: float | None = None, concurrency: int = 2) -> dict | None:
        """Reserve one due execution atomically across any number of dispatchers."""
        now = time.time() if now is None else now
        with self.connect(True) as db:
            active = db.execute("SELECT count(*) FROM schedule_runs WHERE status IN ('starting','running')").fetchone()[0]
            if active >= concurrency:
                return None
            row = db.execute("""SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id
                WHERE r.status IN ('queued','retry') AND r.ready_at<=? AND s.enabled=1 AND s.deleted=0
                  AND r.version=s.version
                ORDER BY r.ready_at,r.created_at LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            db.execute("UPDATE schedule_runs SET status='starting',attempt=attempt+1,lease_until=? WHERE id=?", (now + LEASE_SECONDS, row['id']))
            run_id = row['id']
        return self.run(run_id)

    def start(self, run_id: str, pid: int, now: float | None = None) -> bool:
        """A worker rechecks plan version and pause/delete state before executing."""
        now = time.time() if now is None else now
        with self.connect(True) as db:
            row = db.execute('''SELECT r.* FROM schedule_runs r JOIN schedules s ON s.id=r.schedule_id
                WHERE r.id=? AND r.status='starting' AND s.enabled=1 AND s.deleted=0 AND r.version=s.version''', (run_id,)).fetchone()
            if not row:
                return False
            db.execute("UPDATE schedule_runs SET status='running',pid=?,started_at=?,lease_until=? WHERE id=?", (pid, now, now + LEASE_SECONDS, run_id))
            db.execute("INSERT OR REPLACE INTO schedule_attempts(run_id,attempt,started_at,status) VALUES (?,?,?,'running')", (run_id, row['attempt'], now))
        return True

    def renew(self, run_id: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.connect(True) as db:
            db.execute("UPDATE schedule_runs SET lease_until=? WHERE id=? AND status='running'", (now + LEASE_SECONDS, run_id))

    def finish(self, run_id: str, status: str, error: str = '', output_path: str | None = None,
               now: float | None = None) -> None:
        """Persist the result and bounded backoff; changed/paused plans never retry."""
        now = time.time() if now is None else now
        with self.connect(True) as db:
            run = db.execute('SELECT * FROM schedule_runs WHERE id=?', (run_id,)).fetchone()
            if not run or run['status'] not in ('running', 'starting'):
                return
            plan = db.execute('SELECT * FROM schedules WHERE id=?', (run['schedule_id'],)).fetchone()
            retry = (status in ('failed', 'interrupted') and run['attempt'] <= run['max_retries']
                     and plan and plan['enabled'] and not plan['deleted'] and plan['version'] == run['version'])
            delay = min(60 * 5 ** max(0, run['attempt'] - 1), 3600)
            db.execute('''INSERT OR REPLACE INTO schedule_attempts
                (run_id,attempt,started_at,finished_at,status,error,output_path) VALUES (?,?,?,?,?,?,?)''',
                       (run_id, run['attempt'], run['started_at'], now, status, error[:2000], output_path))
            db.execute('''UPDATE schedule_runs SET status=?,error=?,output_path=?,finished_at=?,
                          lease_until=NULL,pid=NULL,ready_at=? WHERE id=?''',
                       ('retry' if retry else status, error[:2000], output_path, now,
                        now + delay if retry else now, run_id))

    def expired(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM schedule_runs WHERE status IN ('starting','running') AND lease_until<?", (now,))]

    def heartbeat(self, payload: dict) -> None:
        """Persist dispatcher health independently of workers and the web server."""
        import json
        with self.connect(True) as db:
            db.execute("INSERT OR REPLACE INTO scheduler_meta(key,value) VALUES ('heartbeat',?)", (json.dumps(payload),))

    def health(self, now: float | None = None) -> dict:
        import json
        now = time.time() if now is None else now
        with self.connect() as db:
            row = db.execute("SELECT value FROM scheduler_meta WHERE key='heartbeat'").fetchone()
        data = json.loads(row[0]) if row else {}
        age = max(0, now - data['checked_at']) if data.get('checked_at') else None
        online = age is not None and age < 90 and data.get('status') == 'ok'
        return {**data, 'online': online, 'age_seconds': round(age, 1) if age is not None else None,
                'detail': '执行器在线，每 10 秒检查到期任务' if online else data.get('error') or '执行器尚未启动或心跳中断'}
