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
    -e "s|@SCRIPT_DIR@|$SCRIPT_DIR|g" \
    -e "s|@PYTHON@|$PYTHON_BIN|g" \
    -e "s|@LOG_DIR@|$LOG_DIR|g" \
    "$PLIST_SRC/$label.plist" > "$AGENT_DIR/$label.plist"
}

# The gateway is opt-in: it only earns a slot in ~/Library/LaunchAgents once
# gateway.enabled is true in config/web.yaml. Installing it unconditionally
# would leave a process holding port 8765 that the user never asked for.
gateway_enabled() {
  "$PYTHON_BIN" -c "
import sys, yaml
from pathlib import Path
cfg = Path(sys.argv[1])
if not cfg.is_file():
    sys.exit(1)
data = yaml.safe_load(cfg.read_text(encoding='utf-8')) or {}
sys.exit(0 if (data.get('gateway') or {}).get('enabled') else 1)
" "$REPO/config/web.yaml" 2>/dev/null
}

if [[ "${1:-}" == "--uninstall" ]]; then
  for label in "${LABELS[@]}" com.arec.gateway com.arec.maintenance; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
    rm -f "$AGENT_DIR/$label.plist"
    echo "removed $label"
  done
  echo "Also remove the old crontab entry with: crontab -e"
  exit 0
fi

mkdir -p "$AGENT_DIR" "$LOG_DIR"
TO_INSTALL=("${LABELS[@]}" com.arec.maintenance)
if gateway_enabled; then
  TO_INSTALL+=(com.arec.gateway)
else
  echo "note: gateway.enabled is false, so com.arec.gateway was not installed."
  echo "      set gateway.enabled: true in config/web.yaml to keep a permanent"
  echo "      URL that revives the app, and re-run this script."
fi

for label in "${TO_INSTALL[@]}"; do
  render "$label"
  # A malformed plist makes launchctl fail with a bare I/O error and the agent
  # simply does not exist — which is exactly the silent failure this whole
  # mechanism exists to prevent. Validate before loading.
  if ! /usr/bin/plutil -lint "$AGENT_DIR/$label.plist" >/dev/null 2>&1; then
    echo "ERROR: $label.plist is not valid plist:" >&2
    /usr/bin/plutil -lint "$AGENT_DIR/$label.plist" >&2 || true
    exit 1
  fi
  # bootout first so an edited plist actually takes effect on reinstall.
  # launchctl returns before the label is really gone, and bootstrapping into
  # that window fails -- which silently left the gateway unloaded and the
  # bookmarked URL dead. Wait for it, then retry once.
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    launchctl print "$DOMAIN/$label" >/dev/null 2>&1 || break
    sleep 0.3
  done

  loaded=0
  for attempt in 1 2 3; do
    if launchctl bootstrap "$DOMAIN" "$AGENT_DIR/$label.plist" 2>/dev/null; then
      loaded=1
      break
    fi
    # The port may still be held by the process we just unloaded.
    sleep 1
  done
  if [ "$loaded" -ne 1 ]; then
    echo "ERROR: could not load $label" >&2
    echo "  plist: $AGENT_DIR/$label.plist" >&2
    launchctl print "$DOMAIN/$label" 2>&1 | head -5 >&2 || true
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
