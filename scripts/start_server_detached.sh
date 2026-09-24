#!/usr/bin/env bash
# Start the web server fully detached and return immediately.
#
# Called by the macOS launcher applet through `do shell script`, which waits
# for the command to finish. A `nohup ... &` chain written inline inside
# `do shell script` makes AppleScript block until the server exits, so the
# detaching has to happen in a real shell script that exits right away.
#
# Usage: bash scripts/start_server_detached.sh
# Writes: <project>/state/server.pid (run_web.py maintains it), logs/app_launcher.log

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="@PYTHON_BIN@"
LOG_FILE="$PROJECT_DIR/logs/app_launcher.log"
PID_FILE="$PROJECT_DIR/state/server.pid"

if [[ ! -x "$PYTHON_BIN" ]]; then
  # Fall back to the interpreter that is running this script's sibling venv.
  if [[ -x "$PROJECT_DIR/../.AI_research/bin/python" ]]; then
    PYTHON_BIN="$PROJECT_DIR/../.AI_research/bin/python"
  else
    echo "python not found at $PYTHON_BIN" >&2
    exit 1
  fi
fi

mkdir -p "$PROJECT_DIR/logs" "$PROJECT_DIR/state"

cd "$PROJECT_DIR"

# A stale pid file from a crashed run would make the launcher think a server is
# still around; clear it before handing over to run_web.py.
rm -f "$PID_FILE"

{
  nohup "$PYTHON_BIN" run_web.py serve >> "$LOG_FILE" 2>&1 < /dev/null &
  disown || true
}

# Do not wait for the server: the caller returns to the user immediately.
exit 0
