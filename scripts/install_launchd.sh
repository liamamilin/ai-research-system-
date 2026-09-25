#!/usr/bin/env bash
# Install the AREC launchd agents (daily 06:00 + 30-minute catch-up).
#
# launchd instead of cron: on this machine cron accepted a crontab and then
# never executed anything, not even a per-minute probe. launchd dispatches
# reliably, runs jobs missed while the machine was asleep, and can be inspected
# with `launchctl print`.
#
# Usage:
#   bash scripts/install_launchd.sh            # install (idempotent)
#   bash scripts/install_launchd.sh --uninstall
#   bash scripts/install_launchd.sh --verify   # prove launchd dispatches here

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$REPO/scripts"
PLIST_SRC="$SCRIPT_DIR/launchd"
AGENT_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$REPO/logs"
DOMAIN="gui/$(id -u)"

PYTHON_BIN="$REPO/../.AI_research/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="$(command -v python3)"

LABELS=(com.arec.pipeline.daily com.arec.pipeline.catchup)

render() {
  local label="$1"
  /usr/bin/sed \
    -e "s|@REPO@|$REPO|g" \
    -e "s|@SCRIPT@|$SCRIPT_DIR|g" \
    -e "s|@PYTHON@|$PYTHON_BIN|g" \
    -e "s|@LOG_DIR@|$LOG_DIR|g" \
    "$PLIST_SRC/$label.plist" > "$AGENT_DIR/$label.plist"
}

if [[ "${1:-}" == "--uninstall" ]]; then
  for label in "${LABELS[@]}"; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$AGENT_DIR/$label.plist"
    echo "removed $label"
  done
  echo "Also remove the old crontab entry with: crontab -e"
  exit 0
fi

mkdir -p "$AGENT_DIR" "$LOG_DIR"
for label in "${LABELS[@]}"; do
  render "$label"
  # A malformed plist makes launchctl fail with a bare I/O error and the agent
  # simply does not exist — which is exactly the silent failure this whole
  # mechanism exists to prevent. Validate before loading.
  if ! /usr/bin/plutil -lint "$AGENT_DIR/$label.plist" >/dev/null 2>&1; then
    echo "ERROR: $label.plist is not valid plist:" >&2
    /usr/bin/plutil -lint "$AGENT_DIR/$label.plist" >&2 || true
    exit 1
  fi
  # bootout first so an edited plist actually takes effect on reinstall
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  if ! launchctl bootstrap "$DOMAIN" "$AGENT_DIR/$label.plist" 2>/dev/null; then
    echo "ERROR: could not load $label" >&2
    echo "  plist: $AGENT_DIR/$label.plist" >&2
    exit 1
  fi
  echo "installed $label"
done

echo ""
echo "verify the schedule:"
for label in "${LABELS[@]}"; do
  echo "  launchctl print $DOMAIN/$label | rg -e 'state' -e 'runs' -e 'run interval' -e 'Hour'"
done
echo ""
echo "catch-up decision right now:"
"$PYTHON_BIN" "$SCRIPT_DIR/ensure_round.py" --dry-run
echo ""
echo "logs: $LOG_DIR/launchd_daily.log, $LOG_DIR/launchd_catchup.log"
echo "remove the cron entry (it is dead weight and can mislead the watchdog):"
echo "  crontab -l | grep -v 'cron_id: practical_ai_intelligence' | grep -v run_practical_intelligence | crontab -"
