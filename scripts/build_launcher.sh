#!/usr/bin/env bash
# Compile the macOS launcher applet from scripts/launcher.applescript.in.
#
# Usage:
#   bash scripts/build_launcher.sh                 # build into the repo root
#   bash scripts/build_launcher.sh ~/Applications  # build and install there
#
# The generated .app bakes in absolute paths (project dir, python, log file),
# so rebuild it if the repository or virtualenv moves.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$BASE_DIR/scripts/launcher.applescript.in"
RENDERED="$BASE_DIR/scripts/.launcher.rendered.applescript"
STARTER_TEMPLATE="$BASE_DIR/scripts/start_server_detached.sh"
APP_NAME="AI Research Console.app"
DEST_DIR="${1:-$BASE_DIR}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: the launcher is a macOS applet; nothing to do on $(uname -s)." >&2
  exit 1
fi

PYTHON_BIN="$BASE_DIR/../.AI_research/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

mkdir -p "$DEST_DIR" "$BASE_DIR/logs"

# The starter script needs the same interpreter path baked in.
STARTER="$BASE_DIR/scripts/.start_server.rendered.sh"
/usr/bin/sed -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" "$STARTER_TEMPLATE" > "$STARTER"
chmod +x "$STARTER"

# @...@ placeholders are filled per machine; a stale absolute path is the main
# reason the launcher would silently fail after a clone or a move.
/usr/bin/sed \
  -e "s|@PROJECT_DIR@|$BASE_DIR|g" \
  -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" \
  -e "s|@LOG_FILE@|$BASE_DIR/logs/app_launcher.log|g" \
  -e "s|@START_SCRIPT@|$STARTER|g" \
  "$TEMPLATE" > "$RENDERED"

TARGET="$DEST_DIR/$APP_NAME"
rm -rf "$TARGET"
# -s (stay-open) is required: without it the applet quits as soon as `run`
# returns, so `on idle` never fires and the 30-minute watchdog is dead code.
/usr/bin/osacompile -s -o "$TARGET" "$RENDERED"
rm -f "$RENDERED"

# Give it an icon so it is not the default AppleScript droplet.
ICON="$BASE_DIR/scripts/launcher.icns"
if [[ -f "$ICON" ]]; then
  /usr/bin/codesign --force --sign - "$TARGET" 2>/dev/null || true
  /usr/bin/cp "$ICON" "$TARGET/Contents/Resources/applet.icns" 2>/dev/null || true
  /usr/bin/codesign --force --sign - "$TARGET" 2>/dev/null || true
fi

echo "built $TARGET"
echo "paths: project=$BASE_DIR"
echo "       python=$PYTHON_BIN"
echo "       log=$BASE_DIR/logs/app_launcher.log"
