#!/usr/bin/env bash
# Build script: install deps, build frontend, compile assets.
# Usage: bash scripts/build.sh [production|staging]

set -euo pipefail

ENV="${1:-production}"
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Building AI Research Console ($ENV) ==="
echo ""

# 1. Python dependencies
echo ">>> Python deps..."
pip install -r "$BASE_DIR/requirements.txt" -q

# 2. Frontend
echo ">>> Frontend deps..."
cd "$BASE_DIR/ui"
npm install --silent

echo ">>> Frontend build..."
npm run build 2>&1 | tail -3

cd "$BASE_DIR"

# 3. Copy deploy configs
cp config/web.yaml.example config/web.yaml 2>/dev/null || true

# 4. Verify
echo ""
echo ">>> Verification..."
python -c "from web.server import create_app; create_app(); print('  ✓ Backend OK')"

if [ -d "ui/dist" ]; then
    echo "  ✓ Frontend OK (ui/dist)"
fi

echo ""
echo "=== Build complete ==="
echo ""
echo "To start: AI_RESEARCH_ENV=$ENV python run_web.py serve"
