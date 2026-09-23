#!/usr/bin/env bash
#
# Practical AI Intelligence Pipeline
#
# Orchestrates the 10-job information matrix:
#   P0 Planner          →  P1-P6 parallel radars  →  P7-P8 analysis  →  P9 synthesis
#
# Usage:
#   ./scripts/run_practical_intelligence.sh
#
# Cron (daily 06:00), adjust PROJECT_DIR / venv path:
#   0 6 * * * cd /path/to/Base_CodingCLi && export PATH="$HOME/.AI_research/bin:$PATH" && bash scripts/run_practical_intelligence.sh >> logs/cron_pipeline.log 2>&1
#
# Environment:
#   LLM_API_KEY         — LLM API key (referenced by ai.api_key_env in
#                         system.yaml; leave empty for local endpoints).
#   PARALLEL_API_KEY    — Parallel Search API key (search.api_key_env).
#                         Both are set in the project .env file (loaded by run.py).
#

set -o pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Detect project Python (venv at project root's parent)
if [ -x "$BASE_DIR/../.AI_research/bin/python3" ]; then
    PYTHON="$BASE_DIR/../.AI_research/bin/python3"
else
    PYTHON="python3"
fi

RUN="$PYTHON run.py"
LOG_DIR="$BASE_DIR/logs"
TIMESTAMP="$(date '+%Y-%m-%d_%H-%M-%S')"
LOG_FILE="$LOG_DIR/practical_intelligence_${TIMESTAMP}.log"

# Ensure log directory exists
mkdir -p "$LOG_DIR"

exec > >(tee -a "$LOG_FILE") 2>&1

echo ""
echo "=============================================="
echo " Practical AI Intelligence Pipeline"
echo " Started: $(date)"
echo " Base:    $BASE_DIR"
echo " Log:     $LOG_FILE"
echo "=============================================="
echo ""

# ------------------------------------------------------------------
# Helper: run a single job with stage label
# ------------------------------------------------------------------
run_stage() {
    local stage="$1"
    shift
    echo ""
    echo "  >>> [$stage] $*"
    cd "$BASE_DIR" && $RUN "$@" 2>&1
    local rc=$?
    if [ $rc -eq 0 ]; then
        echo "  ✓ [$stage] completed"
    else
        echo "  ✗ [$stage] FAILED (exit code $rc)"
    fi
    return $rc
}

# Track overall status
ALL_OK=true

# ------------------------------------------------------------------
# Budget guard (skip with SKIP_BUDGET_CHECK=1)
# ------------------------------------------------------------------
if [ "${SKIP_BUDGET_CHECK:-0}" != "1" ]; then
    if ! $RUN --check-budget; then
        echo "  ✗ 月度预算已用完，跳过本轮（SKIP_BUDGET_CHECK=1 可强制运行）"
        exit 1
    fi
fi

# ------------------------------------------------------------------
# Stage 1: Collection Plan
# ------------------------------------------------------------------
run_stage "P0" "practical_ai_intelligence/00_collection_planner" || ALL_OK=false

# ------------------------------------------------------------------
# Stage 2: Radar Collection (P1-P6, run in parallel)
# ------------------------------------------------------------------
echo ""
echo "  --- [Stage 2] Starting parallel radars (P1-P6, max ${MAX_PARALLEL} at a time) ---"

MAX_PARALLEL="${MAX_PARALLEL:-3}"
parallel_ok=true
pids=()

run_radar() {
    run_stage "$1" "$2" &
    pids+=($!)
    if [ ${#pids[@]} -ge "$MAX_PARALLEL" ]; then
        for pid in "${pids[@]}"; do
            wait "$pid" || parallel_ok=false
        done
        pids=()
    fi
}

run_radar "P1" "practical_ai_intelligence/01_model_and_pricing_radar"
run_radar "P2" "practical_ai_intelligence/02_ai_coding_tools_radar"
run_radar "P3" "practical_ai_intelligence/03_agent_workflow_radar"
run_radar "P4" "practical_ai_intelligence/04_project_understanding_radar"
run_radar "P5" "practical_ai_intelligence/05_context_rag_memory_radar"
run_radar "P6" "practical_ai_intelligence/06_infra_and_eval_radar"

echo "  Waiting for P1-P6 to complete..."
for pid in "${pids[@]}"; do
    wait "$pid" || parallel_ok=false
done

if $parallel_ok; then
    echo "  ✓ [Stage 2] All radars completed"
else
    echo "  ⚠ [Stage 2] Some radars had failures (continuing)"
    ALL_OK=false
fi

# ------------------------------------------------------------------
# Stage 3: Analysis (P7-P8, sequential, depend on P1-P6 output)
# ------------------------------------------------------------------
echo ""
echo "  --- [Stage 3] Analysis ---"

run_stage "P7" "practical_ai_intelligence/07_product_content_opportunities" || ALL_OK=false
run_stage "P8" "practical_ai_intelligence/08_risk_and_alternatives" || ALL_OK=false

# ------------------------------------------------------------------
# Stage 4: Synthesis (P9, depends on all above)
# ------------------------------------------------------------------
echo ""
echo "  --- [Stage 4] Synthesis ---"

run_stage "P9" "practical_ai_intelligence/09_executive_synthesis_and_actions" || ALL_OK=false

# ------------------------------------------------------------------
# Round finish: artifacts + tracking + digest notification
# ------------------------------------------------------------------
echo ""
echo "  --- [Finish] artifacts / tracking / digest ---"
run_stage "FINISH" --round-finish || echo "  ⚠ [Finish] skipped or failed (non-fatal)"

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------
echo ""
echo "=============================================="
if $ALL_OK; then
    echo " Pipeline completed: ALL STAGES OK"
else
    echo " Pipeline completed: SOME STAGES FAILED"
fi
echo " Log: $LOG_FILE"
echo " Finished: $(date)"
echo "=============================================="
echo ""
