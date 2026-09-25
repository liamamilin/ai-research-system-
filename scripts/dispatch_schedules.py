#!/usr/bin/env python3
"""System entrypoint for generic recurring jobs; no running Web server required."""
from pathlib import Path
import argparse
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default=str(ROOT))
    parser.add_argument('--state-dir')
    parser.add_argument('--jobs-dir')
    parser.add_argument('--config-dir')
    parser.add_argument('--logs-dir')
    parser.add_argument('--worker')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    from dotenv import load_dotenv
    from core.schedule_runner import Runtime, serve, work
    from utils.logger import setup_logger
    root = Path(args.workspace).resolve()
    runtime = Runtime(str(root), *(str(Path(getattr(args, name) or root / default).resolve()) for name, default in
                                  [('state_dir', 'state'), ('jobs_dir', 'jobs'), ('config_dir', 'config'), ('logs_dir', 'logs')]))
    os.chdir(root)
    load_dotenv(root / '.env')
    setup_logger(log_file=str(Path(runtime.logs_dir) / 'scheduler.log'), level='INFO')
    if args.worker:
        work(runtime, args.worker)
    else:
        serve(runtime, once=args.once)


if __name__ == '__main__':
    main()
