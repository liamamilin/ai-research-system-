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
WITH_INDEX=0

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --with-index) WITH_INDEX=1 ;;
        --base) BASE_DIR="$2"; shift ;;
        --dest) DEST_DIR="$2"; shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

if [ "${WITH_INDEX:-0}" -eq 1 ]; then
    FILES+=("${DERIVED[@]}")
fi

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [ -z "$DEST_DIR" ]; then
    DEST_DIR="$BASE_DIR/state/backups/$TIMESTAMP"
fi

FILES=(
    "state/users.db"
    "state/tracking.db"
    "state/events.db"
    # The durable scheduler's own store: every recurring plan, its version
    # counter and all execution evidence. It was missing here, which meant the
    # newest and most operationally load-bearing database was the one thing a
    # restore could not bring back.
    "state/schedules.db"
    "state/report_meta.jsonl"
    "state/pipeline_rounds.json"
    "config/system.yaml"
    "config/web.yaml"
)

# The report index is *derived*: it is rebuilt from output/**.md by
# `python run_web.py reindex`. At 150+ MB it dominated every snapshot (14 of
# them had reached 199 MB), so it is opt-in rather than routine.
DERIVED=(
    "state/reports.db"
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
    if [ "$WITH_INDEX" -eq 0 ]; then
        for rel in "${DERIVED[@]}"; do
            [ -f "$BASE_DIR/$rel" ] || continue
            echo "skip (derived, use --with-index): $rel ($(wc -c < "$BASE_DIR/$rel" | tr -d ' ') bytes)"
        done
    fi
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

# Retention. Backups live on the same disk they protect and nothing pruned
# them, so this directory only ever grew. Keep the most recent N of each kind:
# timestamped snapshot directories, and the per-save YAML copies that
# web/services/yaml_io.py drops here.
KEEP="${AREC_BACKUP_KEEP:-14}"
BACKUP_ROOT="$BASE_DIR/state/backups"
if [ -d "$BACKUP_ROOT" ]; then
    # `head -n -N` is GNU-only; count first and take a positive slice so this
    # works with the BSD tools macOS ships.
    prune_oldest() {
        # $1 = a newline-separated list, oldest first
        printf '%s' "$1" | head -n "$2" | while read -r old; do
            [ -n "$old" ] || continue
            rm -rf "$old"
            echo "pruned old backup: $(basename "$old")"
        done
    }

    # `set -e` plus `pipefail` means a glob with no matches (no snapshots yet,
    # or no YAML backups) would abort the script, so each listing is guarded.
    snaps=$(ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort || true)
    total=$(printf '%s' "$snaps" | grep -c . || true)
    if [ "${total:-0}" -gt "$KEEP" ]; then
        prune_oldest "$snaps" $((total - KEEP))
    fi

    # yaml_io writes <name>_<timestamp>.yaml here on every config save.
    yamls=$(ls -1 "$BACKUP_ROOT"/*.yaml 2>/dev/null | sort || true)
    total=$(printf '%s' "$yamls" | grep -c . || true)
    if [ "${total:-0}" -gt "$KEEP" ]; then
        prune_oldest "$yamls" $((total - KEEP))
    fi
fi
