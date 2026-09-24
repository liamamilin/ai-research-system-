#!/usr/bin/env bash
#
# Back up state databases, metadata and configs into a timestamped folder.
#
# Usage:
#   bash scripts/backup_state.sh                # -> state/backups/<ts>/
#   bash scripts/backup_state.sh --dry-run      # list what would be copied
#   bash scripts/backup_state.sh --base DIR --dest DIR
#
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST_DIR=""
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --base) BASE_DIR="$2"; shift ;;
        --dest) DEST_DIR="$2"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [ -z "$DEST_DIR" ]; then
    DEST_DIR="$BASE_DIR/state/backups/$TIMESTAMP"
fi

FILES=(
    "state/users.db"
    "state/reports.db"
    "state/tracking.db"
    "state/events.db"
    "state/report_meta.jsonl"
    "state/pipeline_rounds.json"
    "config/system.yaml"
    "config/web.yaml"
)

if [ "$DRY_RUN" -eq 1 ]; then
    echo "backup target: $DEST_DIR"
    for rel in "${FILES[@]}"; do
        if [ -f "$BASE_DIR/$rel" ]; then
            echo "would copy: $rel ($(wc -c < "$BASE_DIR/$rel" | tr -d ' ') bytes)"
        else
            echo "skip (missing): $rel"
        fi
    done
    exit 0
fi

mkdir -p "$DEST_DIR"
copied=0
for rel in "${FILES[@]}"; do
    src="$BASE_DIR/$rel"
    [ -f "$src" ] || continue
    name="$(basename "$rel")"
    case "$name" in
        *.db)
            if command -v sqlite3 >/dev/null 2>&1 \
               && sqlite3 "$src" ".backup '$DEST_DIR/$name'" 2>/dev/null; then
                :
            else
                cp "$src" "$DEST_DIR/$name"
            fi
            ;;
        *)
            cp "$src" "$DEST_DIR/$name"
            ;;
    esac
    copied=$((copied + 1))
done

if [ "$copied" -eq 0 ]; then
    rmdir "$DEST_DIR" 2>/dev/null || true
    echo "nothing to back up under $BASE_DIR"
    exit 0
fi

echo "backed up $copied file(s) -> $DEST_DIR"
