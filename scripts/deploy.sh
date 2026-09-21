#!/usr/bin/env bash
# Deploy script: build + copy to target + restart service.
# Usage: bash scripts/deploy.sh /opt/ai-research

set -euo pipefail

TARGET="${1:-/opt/ai-research}"
BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$TARGET" = "/opt/ai-research" ] && [ ! -d "$TARGET" ]; then
    echo "Target directory $TARGET does not exist."
    echo "Usage: bash scripts/deploy.sh /path/to/deploy"
    exit 1
fi

echo "=== Deploying AI Research Console ==="
echo "  Source: $BASE_DIR"
echo "  Target: $TARGET"
echo ""

# 1. Build
bash "$BASE_DIR/scripts/build.sh"

# 2. Copy files
echo ">>> Copying files..."
rsync -av --delete \
    --exclude='node_modules' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='.env' \
    "$BASE_DIR/" "$TARGET/"

# 3. Create env if missing
if [ ! -f "$TARGET/.env" ]; then
    echo ">>> Creating .env (edit this file with your secrets)"
    cp "$TARGET/.env.example" "$TARGET/.env"
fi

# 4. Restart service
if command -v systemctl &> /dev/null; then
    echo ">>> Restarting service..."
    sudo systemctl daemon-reload
    sudo systemctl restart ai-research
    sudo systemctl status ai-research --no-pager | head -5
else
    echo ">>> Service manager not found. Start manually:"
    echo "    cd $TARGET && AI_RESEARCH_ENV=production python run_web.py serve"
fi

echo ""
echo "=== Deploy complete ==="
echo "  Target: $TARGET"
