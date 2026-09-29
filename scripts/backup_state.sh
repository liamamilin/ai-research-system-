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

# Database snapshots go through the SQLite backup API, which needs an
# interpreter. The project's own virtualenv is preferred so a snapshot is never
# taken by a different Python than the one that will read it back.
if [ -x "$BASE_DIR/../.AI_research/bin/python" ]; then
    PY="$BASE_DIR/../.AI_research/bin/python"
else
    PY="$(command -v python3 || true)"
fi

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

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
if [ -z "$DEST_DIR" ]; then
    DEST_DIR="$BASE_DIR/state/backups/$TIMESTAMP"
fi

# The report index is *derived*: it is rebuilt from output/**.md by
# `python run_web.py reindex`. At 150+ MB it dominated every snapshot (14 of
# them had reached 199 MB), so it is opt-in rather than routine.
#
# Both arrays are defined before the flag that reads them. That block used to
# sit above the definitions, so `--with-index` died with
# "DERIVED[@]: unbound variable" on the dry run and on the real run alike: the
# largest database here could not be backed up at all, and no test mentioned
# the flag.
DERIVED=(
    "state/reports.db"
)

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

if [ "${WITH_INDEX:-0}" -eq 1 ]; then
    FILES+=("${DERIVED[@]}")
fi


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
verify_failed=0
for rel in "${FILES[@]}"; do
    src="$BASE_DIR/$rel"
    [ -f "$src" ] || continue
    name="$(basename "$rel")"
    case "$name" in
        *.db)
            # Snapshot through the SQLite backup API rather than copying the
            # file. Every database here is in WAL mode, and a plain `cp` of a
            # WAL database copies the *main file only*: whatever is still in the
            # `-wal` sidecar is not in the snapshot, so the backup opens
            # cleanly and is quietly missing recent writes. The old code only
            # reached `cp` when the sqlite3 CLI was missing, which is exactly
            # the case where nobody would notice.
            #
            # Python is used rather than the sqlite3 CLI because the project
            # already requires it; the CLI is an optional system tool.
            if ! "$PY" - "$src" "$DEST_DIR/$name" <<'PYEOF'
import sqlite3
import sys

src, dest = sys.argv[1], sys.argv[2]


def row_counts(conn):
    """Rows per table, so 'the snapshot opened' and 'the snapshot is whole'
    are not mistaken for the same statement."""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for t in tables}


# Opened plainly, not with `mode=ro`: a WAL database cannot be opened read-only
# unless its -shm sidecar is already there, and the source's may not be. The
# connections are read-only in practice -- nothing is written to the source.
try:
    source = sqlite3.connect(src)
    before = row_counts(source)
    with sqlite3.connect(dest) as target:
        source.backup(target)
    after = row_counts(target)
except sqlite3.Error as exc:
    print(f"cannot snapshot {src}: {exc}", file=sys.stderr)
    sys.exit(1)
finally:
    try:
        source.close()
    except Exception:
        pass

# `PRAGMA integrity_check` passes on a truncated but structurally valid file,
# so it cannot tell a complete snapshot from a partial one. Row counts can.
missing = {t: (n, after.get(t)) for t, n in before.items() if after.get(t) != n}
if missing:
    for table, (want, got) in sorted(missing.items()):
        print(f"{table}: source {want} rows, snapshot {got}", file=sys.stderr)
    sys.exit(1)
PYEOF
            then
                echo "WARNING: snapshot of $rel is incomplete or unreadable -- NOT backed up" >&2
                # Remembered, because the read-back pass below only inspects
                # files that landed: without this a database that could not be
                # snapshotted produced a warning and still exited 0, so a
                # scheduled run looked like it had backed everything up.
                verify_failed=1
                continue
            fi
            ;;
        *)
            cp "$src" "$DEST_DIR/$name"
            ;;
    esac
    copied=$((copied + 1))
done

# Read the snapshots back. A backup is only worth having if it opens, and the
# failure mode here -- a truncated copy, a snapshot missing committed rows --
# is invisible until someone tries to restore, which is the worst time to find
# out. This costs a few milliseconds and turns that into a message now.
#
# `verify_failed` is deliberately not reset here: a database that could not be
# snapshotted above never lands in the destination and is skipped below, so
# re-initialising would swallow exactly the failure worth reporting.
for rel in "${FILES[@]}"; do
    name="$(basename "$rel")"
    snap="$DEST_DIR/$name"
    [ -f "$snap" ] || continue
    case "$name" in
        *.db)
            if ! "$PY" - "$snap" <<'PYEOF'
import sqlite3
import sys

try:
    conn = sqlite3.connect(sys.argv[1])
    problems = [r[0] for r in conn.execute("PRAGMA integrity_check")
                if r[0] != "ok"]
    if problems:
        print("; ".join(problems[:3]), file=sys.stderr)
        sys.exit(1)
    conn.close()
except sqlite3.Error as exc:
    print(exc, file=sys.stderr)
    sys.exit(1)
PYEOF
            then
                echo "WARNING: $name did not verify -- treat this snapshot as suspect" >&2
                verify_failed=1
            fi
            ;;
        *)
            if [ ! -s "$snap" ]; then
                echo "WARNING: $name is empty" >&2
                verify_failed=1
            fi
            ;;
    esac
done

if [ "$copied" -eq 0 ]; then
    rmdir "$DEST_DIR" 2>/dev/null || true
    echo "nothing to back up under $BASE_DIR"
    exit 0
fi

echo "backed up $copied file(s) -> $DEST_DIR"

if [ "$verify_failed" -ne 0 ]; then
    echo "snapshot did not fully verify -- see the warnings above" >&2
    exit 1
fi

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
