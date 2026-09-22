#!/usr/bin/env bash
#
# Local CI: compile, unit tests, config validation, frontend type-check + build.
#
# Usage: bash scripts/check.sh
#
set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BASE_DIR"

if [ -x "$BASE_DIR/../.AI_research/bin/python" ]; then
    PY="$BASE_DIR/../.AI_research/bin/python"
else
    PY="python3"
fi

echo "== python compile =="
$PY -m compileall -q core web utils scripts run.py run_web.py

echo "== unit tests =="
$PY -m pytest -q

echo "== config validation =="
$PY run.py --validate | tail -1

if command -v npm >/dev/null 2>&1 && [ -d ui/node_modules ]; then
    echo "== frontend type-check + lint + tests + build =="
    (cd ui && npx tsc --noEmit --noUnusedLocals --noUnusedParameters \
        && npm run lint --silent && npm test --silent && npm run build >/dev/null)
fi

echo ""
echo "ALL CHECKS PASSED"
