#!/usr/bin/env python3
"""
AI Research System - CLI entry point.

Usage:
  python run.py                  # Run all enabled jobs
  python run.py <job> [<job>...] # Run specific job(s)
  python run.py --list           # List all jobs
  python run.py --list-enabled   # List enabled jobs
  python run.py --status         # Show last run status
  python run.py -v <job>         # Verbose mode
  python run.py --validate       # Validate configs
"""

import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

from core import ResearchEngine, load_system_config, list_jobs, load_job
from core.state import StateManager
from utils.logger import setup_logger


def parse_args():
    p = argparse.ArgumentParser(
        description="AI Research System - automated AI research reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""examples:
  python run.py                        Run all enabled jobs
  python run.py daily_ai_agents        Run by basename (auto-finds in subdirs)
  python run.py monitoring/daily_ai_agents  Run by full path
  python run.py job1 job2              Run multiple jobs
  python run.py --list                 List all jobs
  python run.py --list-enabled         List enabled jobs
  python run.py --status               Show last run status for all jobs
  python run.py --status daily_ai_agents  Show status for a specific job
  python run.py -v daily_ai_agents     Verbose mode
  python run.py --validate             Validate configs

Templates: copy from jobs/_templates/ to create new jobs""",
    )
    p.add_argument("jobs", nargs="*", help="Job name(s) to run")
    p.add_argument("--list", action="store_true", help="List all jobs")
    p.add_argument("--list-enabled", action="store_true", help="List enabled jobs")
    p.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    p.add_argument("--validate", action="store_true", help="Validate configuration")
    p.add_argument("--check-budget", action="store_true",
                   help="Check month-to-date spend against budget.monthly_usd_limit "
                        "(exit 1 when exceeded and block_pipeline is on)")
    p.add_argument("--test-notify", action="store_true",
                   help="Send a test notification to every configured channel")
    p.add_argument("--round-finish", nargs="?", const=True, default=False,
                   metavar="DATE",
                   help="Export artifacts, sync tracking and send the digest "
                        "for a finished pipeline round (default: today)")
    p.add_argument("--timeout", "-t", type=int, help="Override AI timeout in seconds (default: from system.yaml)")
    p.add_argument("--status", nargs="?", const=True, default=False,
                   metavar="JOB",
                   help="Show last run status (optionally for a specific job)")
    p.add_argument("--config-dir", default="config", help="Config directory (default: config)")
    p.add_argument("--jobs-dir", default="jobs", help="Jobs directory (default: jobs)")
    return p.parse_args()


def print_jobs(jobs_dir: str, enabled_only: bool = False):
    """Print a formatted job list."""
    names = list_jobs(jobs_dir, enabled_only=enabled_only)
    label = "enabled" if enabled_only else "all"
    if not names:
        print(f"No {label} jobs found.")
        return
    print(f"\n{label.capitalize()} jobs ({len(names)}):")
    print("=" * 50)
    for name in names:
        job = load_job(jobs_dir, name)
        status = "✓" if (job and job.get("enabled", True)) else "✗"
        desc = job.get("description", "") if job else ""
        print(f"  {status} {name}")
        if desc:
            print(f"    {desc}")
    print()


def print_status(job_name: str = None):
    """Print last run status for one or all jobs."""
    if job_name:
        state = StateManager.get(job_name)
        if not state:
            print(f"No run state recorded for '{job_name}'.")
            return
        _print_single_status(job_name, state)
    else:
        all_states = StateManager.list_all()
        if not all_states:
            print("No run states recorded.")
            return
        print(f"\nRun status ({len(all_states)}):")
        print("=" * 50)
        for name, state in all_states.items():
            _print_single_status(name, state, compact=True)
    print()


def _print_single_status(name: str, state: dict, compact: bool = False):
    """Print a single job's status entry."""
    status = state.get("last_status", "?")
    icon = {"success": "✓", "failed": "✗", "skipped": "→"}.get(status, "?")
    last_run = state.get("last_run_at", "?")
    duration = state.get("last_duration_seconds")
    output = state.get("last_output", "")
    error = state.get("last_error", "")

    if compact:
        dur = f" ({int(duration)}s)" if duration else ""
        print(f"  {icon} {name}  —  {status}{dur}  [{last_run}]")
        return

    print(f"\n  Job: {name}")
    print(f"  Status:     {status} {icon}")
    print(f"  Last run:   {last_run}")
    if duration:
        print(f"  Duration:   {int(duration)}s")
    if output:
        print(f"  Output:     {output}")
    if error:
        print(f"  Error:      {error}")


def main():
    args = parse_args()

    log_level = "DEBUG" if args.verbose else "INFO"
    sys_cfg = load_system_config(args.config_dir)
    if args.timeout:
        sys_cfg.setdefault("ai", {})["timeout"] = args.timeout
    log_cfg = sys_cfg.get("logging", {})
    setup_logger(
        log_file=log_cfg.get("file", "logs/ai_research.log"),
        level=log_cfg.get("level", log_level),
    )

    logger = logging.getLogger("ai_research")
    logger.info("=" * 50)
    logger.info("AI Research System started at %s", datetime.now().isoformat())

    if args.validate:
        from scripts.validate import main as validate_main
        sys.exit(validate_main())

    if args.check_budget:
        from core.budget import month_spend
        status = month_spend(sys_cfg)
        if status["limit"] > 0:
            print(f"  budget: ${status['spent']:.2f} / ${status['limit']:.2f} "
                  f"({status['ratio'] * 100:.0f}% of monthly limit)")
        else:
            print(f"  budget: no limit configured (month-to-date ${status['spent']:.2f})")
        if status["exceeded"] and status["block_pipeline"]:
            print("  ✗ monthly budget exceeded — blocking")
            sys.exit(1)
        sys.exit(0)

    if args.round_finish is not False:
        from core.round_finish import finish_round

        date = (args.round_finish if isinstance(args.round_finish, str)
                else datetime.now().strftime("%Y-%m-%d"))
        summary = finish_round(
            date,
            output_dir=(sys_cfg.get("defaults") or {}).get("output_dir", "output"),
            config_dir=args.config_dir,
        )
        if not summary["artifacts"]:
            print(f"  no round documents found for {date}")
            sys.exit(1)
        tracking = summary["tracking"] or {}
        print(f"  ✓ artifacts exported for {date}; tracking: "
              f"{tracking.get('new', 0)} new / {tracking.get('updated', 0)} updated; "
              f"digest notified: {'yes' if summary['notified'] else 'no'}")
        sys.exit(0)

    if args.test_notify:
        from core.notify import send_test

        report = send_test(sys_cfg)
        if not report["enabled"]:
            print("  notifications.enabled is false — nothing sent")
            sys.exit(1)
        if not report["channels"]:
            print("  no notification channel configured "
                  "(webhook_url / wecom / feishu / email)")
            sys.exit(1)
        for channel, ok in report["results"].items():
            print(f"  {'✓' if ok else '✗'} {channel}")
        sys.exit(0 if all(report["results"].values()) else 1)

    engine = ResearchEngine(config_dir=args.config_dir, jobs_dir=args.jobs_dir)

    if args.list:
        print_jobs(args.jobs_dir, enabled_only=False)
        return

    if args.list_enabled:
        print_jobs(args.jobs_dir, enabled_only=True)
        return

    if args.status is not False:
        job_name = args.status if isinstance(args.status, str) else None
        print_status(job_name)
        return

    # Determine job names to run
    names = args.jobs
    if not names:
        names = list_jobs(args.jobs_dir, enabled_only=True)

    if not names:
        print("No jobs to run. Use --list to see available jobs.")
        return

    print(f"\nRunning {len(names)} job(s)...")
    print("=" * 50)
    print(f"Start: {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    results = {}
    for name in names:
        path = engine.run_job(name, verbose=args.verbose)
        results[name] = path
        status = "✓" if path else "✗"
        print(f"  {status} {name}")

    print(f"\nDone: {sum(1 for v in results.values() if v)}/{len(results)} succeeded.")
    logger.info("AI Research System finished.")


if __name__ == "__main__":
    main()
