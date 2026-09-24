"""Safe YAML read/write with ruamel.yaml.

Features:
- ruamel.yaml preserves comments, ordering, block scalars (| >)
- Atomic write via .tmp + os.rename
- Automatic backup to state/backups/{job}_{ts}.yaml
- Mtime-based conflict detection
- Soft validation (warns, never blocks save)
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError

_yaml = YAML()
_yaml.indent(mapping=2, sequence=4, offset=2)
_yaml.width = 120

BACKUP_DIR = "state/backups"

REQUIRED_FIELDS = {"prompt"}
RECOMMENDED_FIELDS = {"name", "enabled", "output"}


def parse_yaml(content: str) -> dict:
    """Parse YAML string into a dict. Raises ValueError on parse error."""
    try:
        data = _yaml.load(content)
    except (ParserError, ScannerError) as e:
        raise ValueError(f"YAML 解析错误: {e}")
    if not isinstance(data, dict):
        raise ValueError("YAML 根结构必须是一个字典")
    return data


def dump_yaml(data: dict) -> str:
    """Serialize a dict to a YAML string (multi-line text keeps literal block style)."""
    from io import StringIO

    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.scalarstring import LiteralScalarString

    if isinstance(data, dict):
        data = CommentedMap(data)
        for key, val in data.items():
            if isinstance(val, str) and "\n" in val:
                data[key] = LiteralScalarString(val)
    buf = StringIO()
    _yaml.dump(data, buf)
    return buf.getvalue()


def validate_yaml(data: dict) -> list[str]:
    """Soft validation. Returns a list of warning messages (empty = no issues)."""
    warnings: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in data or data[field] is None:
            warnings.append(f"缺少必填字段: '{field}'")

    for field in RECOMMENDED_FIELDS:
        if field not in data or data[field] is None:
            warnings.append(f"建议添加字段: '{field}'")

    prompt = data.get("prompt", "")
    if isinstance(prompt, str) and len(prompt.strip()) < 20:
        warnings.append("prompt 内容过短 (< 20 字符)")

    output = data.get("output", "")
    if isinstance(output, str) and not output.strip():
        warnings.append("output 路径为空, 将使用默认路径")

    keywords = data.get("keywords", [])
    if isinstance(keywords, list) and len(keywords) == 0:
        warnings.append("keywords 为空, 可能影响搜索效率")

    return warnings


def validate_output_path(data: dict, output_root: str = "output") -> list[str]:
    """Hard validation of the output target (a write path, not a style issue).

    Returns a list of blocking errors: absolute paths, ``..`` escapes and
    non-Markdown targets would let an editor overwrite source, config or
    state files through a job run.
    """
    errors: list[str] = []
    output = data.get("output", "")
    if not isinstance(output, str) or not output.strip():
        return errors

    runtime = data.get("runtime") or {}
    root = runtime.get("output_root") or output_root
    raw = output.strip()
    if os.path.isabs(raw):
        errors.append("output 必须是相对路径")
        return errors
    resolved = os.path.abspath(os.path.join(root, raw))
    root_abs = os.path.abspath(root)
    if resolved != root_abs and not resolved.startswith(root_abs + os.sep):
        errors.append(f"output 路径超出允许目录 '{root}'")
    if not raw.endswith((".md", ".mdx")):
        errors.append("output 必须以 .md 或 .mdx 结尾")
    bad_vars = [
        v for v in re.findall(r"\{(\w+)\}", raw)
        if v not in {"name", "date", "time", "datetime"}
    ]
    if bad_vars:
        errors.append(f"output 中未知变量: {', '.join(sorted(set(bad_vars)))}")
    return errors


def check_conflict(file_path: str, expected_mtime: float) -> Optional[float]:
    """Check if the file was modified since `expected_mtime`.
    Returns the current mtime if different, None if no conflict.
    """
    try:
        current_mtime = os.path.getmtime(file_path)
    except OSError:
        return None
    if abs(current_mtime - expected_mtime) > 0.01:
        return current_mtime
    return None


def _backup_dir() -> str:
    """Resolve the backup directory from web settings when available."""
    try:
        from web.settings import get_settings
        return os.path.join(get_settings().paths.state_dir, "backups")
    except Exception:  # noqa: BLE001 - CLI usage without web settings
        return BACKUP_DIR


def save_backup(file_path: str, content: str) -> str:
    """Save a backup copy to state/backups/. Returns the backup path."""
    backup_dir = _backup_dir()
    os.makedirs(backup_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    base = Path(file_path).stem
    backup_path = os.path.join(backup_dir, f"{base}_{ts}.yaml")
    try:
        with open(backup_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        raise IOError(f"备份写入失败: {e}")
    return backup_path


def atomic_write(file_path: str, content: str):
    """Atomic write: write to .tmp then rename. Does NOT backup (caller's choice)."""
    tmp_path = file_path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.rename(tmp_path, file_path)
    except OSError as e:
        # Clean up temp file on failure
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise IOError(f"写入文件失败: {e}")


def save_job_yaml(
    file_path: str,
    new_content: str,
    expected_mtime: Optional[float] = None,
    backup: bool = True,
) -> dict:
    """Validate and save a job YAML file.

    Args:
        file_path: Path to the YAML file.
        new_content: New YAML content as a string.
        expected_mtime: If set, fail if file was modified externally.
        backup: Whether to auto-backup the old content before overwriting.

    Returns:
        dict with:
            warnings: list[str] validation warnings
            backup_path: str | None

    Raises:
        ValueError: YAML parsing or validation failure.
        IOError: File write failure.
        FileNotFoundError: file_path does not exist.
    """
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    # Parse & validate new content
    parsed = parse_yaml(new_content)
    warnings = validate_yaml(parsed)

    # Conflict detection
    if expected_mtime is not None:
        conflict = check_conflict(file_path, expected_mtime)
        if conflict is not None:
            raise IOError(
                f"文件已被外部修改 (mtime: {expected_mtime} → {conflict})。"
                f"请刷新后重试。"
            )

    # Read old content and backup BEFORE writing
    old_content = ""
    backup_path = None
    if backup:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                old_content = f.read()
        except OSError:
            pass
        if old_content and old_content != new_content:
            backup_path = save_backup(file_path, old_content)

    # Atomic write (happens after successful backup)
    atomic_write(file_path, new_content)

    return {
        "warnings": warnings,
        "backup_path": backup_path,
    }
