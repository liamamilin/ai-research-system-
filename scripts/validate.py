#!/usr/bin/env python3
"""
Configuration validation script.
Checks job files for correctness (supports subdirectories).
"""

import os
import sys
import yaml

_SKIP_DIRS = {"_templates", "__pycache__", ".git", "node_modules"}


def _walk_jobs(jobs_dir: str) -> list[tuple[str, str]]:
    """Yield (relative_name, absolute_path) for all job YAML files."""
    results = []
    for root, dirs, files in os.walk(jobs_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith("_")]
        for fn in sorted(files):
            if not fn.endswith((".yaml", ".yml")):
                continue
            if fn.startswith("."):
                continue
            file_path = os.path.join(root, fn)
            rel = os.path.relpath(file_path, jobs_dir)
            name = rel.rsplit(".", 1)[0]
            results.append((name, file_path))
    return results


def check_jobs(jobs_dir: str = "jobs") -> bool:
    """Validate all job YAML files (recursively)."""
    if not os.path.isdir(jobs_dir):
        print(f"  ✗ Jobs directory not found: {jobs_dir}")
        return False

    job_files = _walk_jobs(jobs_dir)
    if not job_files:
        print("  ⚠ No job files found")
        return False

    print(f"  Found {len(job_files)} job file(s):")
    errors = []

    for name, path in job_files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                job = yaml.safe_load(f) or {}

            issues = []
            if not job.get("name"):
                issues.append("missing 'name'")
            if not job.get("prompt"):
                issues.append("missing 'prompt'")
            if not job.get("keywords"):
                issues.append("missing 'keywords' (will produce empty search)")

            if issues:
                errors.append(f"{name}: {', '.join(issues)}")
                print(f"    ✗ {name} - {', '.join(issues)}")
            else:
                print(f"    ✓ {name}")

        except yaml.YAMLError as e:
            errors.append(f"{name}: YAML parse error - {e}")
            print(f"    ✗ {name} - YAML error: {e}")
        except Exception as e:
            errors.append(f"{name}: {e}")
            print(f"    ✗ {name} - {e}")

    if errors:
        print(f"\n  {len(errors)} issue(s) found.")
        return False
    print("  All job files valid.")
    return True


def check_system_config(config_dir: str = "config") -> bool:
    """Validate system.yaml."""
    path = os.path.join(config_dir, "system.yaml")
    if not os.path.exists(path):
        print("  ✗ system.yaml not found")
        return False

    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        ai = cfg.get("ai", {})
        model = ai.get("model", "not set")
        timeout = ai.get("timeout", "not set")
        print(f"  ✓ system.yaml - model={model}, timeout={timeout}s")
        return True
    except Exception as e:
        print(f"  ✗ system.yaml error: {e}")
        return False


def main():
    print("=" * 50)
    print("AI Research System - Config Validation")
    print("=" * 50)

    print("\nSystem config:")
    sys_ok = check_system_config()

    print("\nJob files:")
    jobs_ok = check_jobs()

    print("\n" + "=" * 50)
    if sys_ok and jobs_ok:
        print("✓ All checks passed.")
        return 0
    else:
        print("✗ Some checks failed. See details above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
