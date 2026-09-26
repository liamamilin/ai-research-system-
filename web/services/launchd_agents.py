"""Read and manage the two launchd agents that trigger the daily matrix round.

These agents used to be read-only: the schedule page listed them and refused
every edit with "此任务由系统 launchd 管理". That left no way to move the daily
run time or stop the round without a shell. They are ordinary configuration
files, so the page can own them -- with three rules this module keeps:

* an edit is validated (``plutil -lint``) and rolled back if the reload fails,
  so a bad write can never leave the round untriggered;
* a delete moves the plist into ``state/scheduler_backup/`` instead of
  unlinking it, so the page can offer a real restore;
* nothing here reads or writes the developer's real agents unless it is asked
  to -- ``agent_dir`` and ``state_dir`` are parameters, which is what lets the
  tests run without touching ``~/Library/LaunchAgents``.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

__all__ = [
    "LABELS", "KINDS", "LaunchdError", "agent_dir", "plist_path", "read_config",
    "to_cron", "is_loaded", "apply_cron", "set_enabled", "delete", "list_deleted",
    "restore",
]

# The two agents scripts/install_launchd.sh installs for the intelligence matrix.
LABELS: tuple[str, ...] = ("com.arec.pipeline.daily", "com.arec.pipeline.catchup")

# What kind of trigger each label is, which decides how a cron expression maps
# onto it. daily fires at one wall-clock time; catchup polls on an interval.
KINDS: dict[str, str] = {
    "com.arec.pipeline.daily": "daily",
    "com.arec.pipeline.catchup": "interval",
}

# launchd refuses StartInterval below this, so a 1-minute floor is real rather
# than defensive.
_MIN_INTERVAL = 60

# An interval longer than this stops being a useful "did we miss the round?"
# safety net -- the daily agent is the one that actually runs it.
_MAX_INTERVAL = 6 * 3600


class LaunchdError(RuntimeError):
    """A launchd/filesystem operation failed; the message is safe to show an admin.

    Validation problems raise ``ValueError`` instead, so callers can map
    "you typed a bad schedule" (422) apart from "launchd said no" (503).
    """


def _unsupported() -> bool:
    return sys.platform != "darwin"


def agent_dir() -> Path:
    """Where the pipeline agents are installed for the current user."""
    return Path.home() / "Library" / "LaunchAgents"


def plist_path(label: str, directory: Path | None = None) -> Path:
    _require_known(label)
    return (Path(directory) if directory else agent_dir()) / f"{label}.plist"


def _require_known(label: str) -> str:
    if label not in KINDS:
        raise LaunchdError(f"未知的系统任务：{label}")
    return label


def read_config(label: str, directory: Path | None = None) -> dict:
    """The installed plist as a dict, or ``{}`` when it is absent or unreadable."""
    try:
        with open(plist_path(label, directory), "rb") as fh:
            config = plistlib.load(fh)
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {}
    return config if isinstance(config, dict) else {}


def is_loaded(label: str) -> bool:
    """Whether launchd currently has the agent registered in this GUI session."""
    _require_known(label)
    if _unsupported():
        return False
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def to_cron(label: str, config: dict) -> str:
    """The cron-shaped expression that describes this agent's current trigger.

    The schedule page already speaks cron (CronBuilder, preview, summaries), so
    the agents are projected into that vocabulary rather than teaching the UI a
    second one. An agent that is neither a daily time nor a plain interval has
    no cron equivalent, which is reported instead of guessed at.
    """
    _require_known(label)
    if KINDS[label] == "daily":
        calendar = config.get("StartCalendarInterval")
        if not isinstance(calendar, dict) or "Hour" not in calendar:
            return ""
        try:
            hour = int(calendar.get("Hour", 0))
            minute = int(calendar.get("Minute", 0))
        except (TypeError, ValueError):
            return ""
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return ""
        return f"{minute} {hour} * * *"
    try:
        seconds = int(config.get("StartInterval", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if seconds < _MIN_INTERVAL:
        return ""
    return f"*/{max(1, round(seconds / 60))} * * * *"


def _parse_daily(expression: str) -> dict:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("每日任务的频率格式应为「分 时 * * *」，例如 0 6 * * *")
    minute, hour, dom, month, dow = fields
    if (dom, month, dow) != ("*", "*", "*"):
        raise ValueError("每日任务只支持每天固定时间，请使用「分 时 * * *」")
    if not minute.isdigit() or not hour.isdigit():
        raise ValueError("每日任务的小时和分钟必须是数字，例如 0 6 * * *")
    if not (0 <= int(minute) <= 59) or not (0 <= int(hour) <= 23):
        raise ValueError("每日任务的时间超出范围（时 0-23，分 0-59）")
    return {"StartCalendarInterval": {"Hour": int(hour), "Minute": int(minute)}}


def _parse_interval(expression: str) -> dict:
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("补跑任务的频率格式应为「*/分钟 * * * *」，例如 */30 * * * *")
    step = re.fullmatch(r"\*/(\d{1,3})", fields[0])
    if not step or fields[1:] != ["*", "*", "*", "*"]:
        raise ValueError("补跑任务只支持固定间隔，请使用「*/分钟 * * * *」，例如 */30 * * * *")
    minutes = int(step.group(1))
    seconds = minutes * 60
    if seconds < _MIN_INTERVAL:
        raise ValueError("补跑间隔不能小于 1 分钟")
    if seconds > _MAX_INTERVAL:
        raise ValueError("补跑间隔不能超过 6 小时，请用「每日任务」表达更长的周期")
    return {"StartInterval": seconds}


def _launchctl(*args: str) -> None:
    if _unsupported():
        raise LaunchdError("系统任务管理仅支持 macOS")
    try:
        result = subprocess.run(["/bin/launchctl", *args],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchdError(f"系统调度器操作失败：{exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        raise LaunchdError(f"系统调度器操作失败：{detail or ' '.join(args)}")


def _lint(path: Path) -> None:
    """Refuse to load a plist launchctl would reject with a bare I/O error."""
    linter = Path("/usr/bin/plutil")
    if not linter.exists():
        return
    try:
        result = subprocess.run([str(linter), "-lint", str(path)],
                                capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchdError(f"plist 校验失败：{exc}") from exc
    if result.returncode:
        raise LaunchdError(f"plist 格式无效：{(result.stderr or result.stdout).strip()}")


def _install(path: Path, config: dict, previous: bytes | None, was_loaded: bool) -> None:
    """Write the plist and (re)load it, restoring the previous file on failure.

    Mirrors core/schedule_host.install: a reload that fails must not leave the
    matrix with no trigger at all, which is the failure this whole page exists
    to prevent.
    """
    temporary = path.with_suffix(".plist.tmp")
    temporary.write_bytes(plistlib.dumps(config))
    try:
        _lint(temporary)
    except LaunchdError:
        temporary.unlink(missing_ok=True)
        raise
    if was_loaded:
        _launchctl("bootout", f"gui/{os.getuid()}/{path.stem}")
    temporary.replace(path)
    try:
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
    except LaunchdError:
        if previous is not None:
            path.write_bytes(previous)
            if was_loaded:
                _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        else:
            path.unlink(missing_ok=True)
        raise


def _write(label: str, mutate, directory: Path | None = None) -> dict:
    """Apply ``mutate`` to the installed config and reload, or change nothing."""
    _require_known(label)
    path = plist_path(label, directory)
    if not path.exists():
        raise LaunchdError(f"系统任务未安装：{label}（请运行 bash scripts/install_launchd.sh）")
    previous = path.read_bytes()
    config = plistlib.loads(previous)
    if not isinstance(config, dict):
        raise LaunchdError(f"系统任务配置无法解析：{label}")
    was_loaded = is_loaded(label)
    updated = mutate(dict(config))
    if updated == config:
        return config
    _install(path, updated, previous, was_loaded)
    return updated


def apply_cron(label: str, expression: str, directory: Path | None = None) -> dict:
    """Move this agent's trigger to ``expression``, keeping every other key.

    Raises ``ValueError`` for an unusable expression (nothing is touched) and
    ``LaunchdError`` if launchd refuses the reload (the previous plist is put
    back).
    """
    # Validate the label before indexing KINDS, so an unknown id is a named
    # error rather than a bare KeyError out of a dict lookup.
    _require_known(label)
    expression = " ".join(str(expression or "").split())
    if not expression:
        raise ValueError("请填写执行频率")
    patch = _parse_daily(expression) if KINDS[label] == "daily" else _parse_interval(expression)

    def mutate(config: dict) -> dict:
        # The other trigger must not linger: an agent carrying both a calendar
        # time and an interval fires on whichever comes first, which is not what
        # the operator just asked for.
        for key in ("StartCalendarInterval", "StartInterval"):
            if key not in patch:
                config.pop(key, None)
        config.update(patch)
        return config

    return _write(label, mutate, directory)


def set_enabled(label: str, enabled: bool, directory: Path | None = None) -> dict:
    """Pause (unload, keep the plist) or resume (load) this agent.

    A pause is deliberately not a delete: the trigger is still on disk, so
    resuming cannot lose the schedule the operator configured.
    """
    _require_known(label)
    path = plist_path(label, directory)
    if not path.exists():
        raise LaunchdError(f"系统任务未安装：{label}（请运行 bash scripts/install_launchd.sh）")
    if enabled:
        if not is_loaded(label):
            _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
    elif is_loaded(label):
        _launchctl("bootout", f"gui/{os.getuid()}/{label}")
    return read_config(label, directory)


def backup_root(state_dir: str | os.PathLike) -> Path:
    return Path(state_dir) / "scheduler_backup"


def delete(label: str, state_dir: str | os.PathLike,
           directory: Path | None = None) -> dict:
    """Unload the agent and move its plist into a restorable backup.

    The round stops being triggered, which is the point -- but nothing is
    unlinked, so the page can put it back with one click.
    """
    _require_known(label)
    path = plist_path(label, directory)
    if not path.exists():
        raise LaunchdError(f"系统任务未安装：{label}")
    config = read_config(label, directory)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = backup_root(state_dir) / stamp
    target.mkdir(parents=True, exist_ok=True)
    if is_loaded(label):
        _launchctl("bootout", f"gui/{os.getuid()}/{label}")
    shutil.move(str(path), str(target / f"{label}.plist"))
    (target / "meta.json").write_text(json.dumps({
        "label": label,
        "kind": KINDS[label],
        "deleted_at": stamp,
        "schedule": to_cron(label, config),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"label": label, "backup": str(target), "schedule": to_cron(label, config)}


def list_deleted(state_dir: str | os.PathLike) -> list[dict]:
    """Deleted system tasks, newest first, so the page can offer a restore."""
    root = backup_root(state_dir)
    if not root.is_dir():
        return []
    found: list[dict] = []
    for entry in sorted(root.iterdir(), reverse=True):
        meta = entry / "meta.json"
        if not meta.is_file():
            continue
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        label = data.get("label")
        if label in KINDS and (entry / f"{label}.plist").is_file():
            # restore() needs the directory it came from, and meta.json does not
            # record it -- the entry's own location is the authority.
            found.append({**data, "backup": str(entry), "installed": False})
    return found


def restore(label: str, state_dir: str | os.PathLike,
            directory: Path | None = None) -> dict:
    """Put a deleted system task back and load it again."""
    _require_known(label)
    for entry in list_deleted(state_dir):
        if entry["label"] != label:
            continue
        source = Path(entry["backup"]) / f"{label}.plist"
        if not source.is_file():
            raise LaunchdError(f"备份已丢失：{source}")
        path = plist_path(label, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise LaunchdError(f"{label} 已经存在，请先删除再恢复")
        shutil.move(str(source), str(path))
        _lint(path)
        if not is_loaded(label):
            _launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        return {"label": label, "installed": True, "schedule": entry.get("schedule", "")}
    raise LaunchdError(f"没有找到 {label} 的备份")
