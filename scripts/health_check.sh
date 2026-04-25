#!/bin/bash
# ============================================================
# scripts/health_check.sh — R74 dual-system prod monitoring
#
# Per memory rule (feedback_iteration_monitoring): every /loop cycle
# starts with this. Aggregates BOTH SUPERTREND + SHADOW health into
# a single verdict.
#
# Returns:
#   exit 0 = healthy or only-known-degraded (alerts, no hard problems)
#   exit 1 = at least one hard problem (per evaluate_supertrend +
#            evaluate_shadow rules in health_check_core.py)
#
# Hard problem (block iteration):
#   SUPERTREND: bot.state!=running, n_pairs<=0, journal not OK,
#               0 evaluations, errors{}
#   SHADOW:     health=red, RED_PIPELINE / ZERO_TRADEABLE_WALLETS /
#               ALL_SKIPPED_NO_PAPER alerts
#
# Known-degraded (don't block, just inform):
#   SUPERTREND: NO_FIRES_24H (chop regime, expected)
#   SHADOW:     COLD_START_DRIFT_DOMINANT (R72 forward-only fix), latency
#
# Usage:
#   bash scripts/health_check.sh                 # SSH to VPS
#   bash scripts/health_check.sh --local         # localhost (dev)
#   bash scripts/health_check.sh --quiet         # exit code only
# ============================================================
set -euo pipefail

VPS_HOST="${VPS_HOST:-root@187.127.100.77}"
LOCAL=0
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --local) LOCAL=1 ;;
        --quiet) QUIET=1 ;;
    esac
done

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TMP_SUP=$(mktemp)
TMP_SHA=$(mktemp)
trap "rm -f $TMP_SUP $TMP_SHA" EXIT

# -- Fetch SUPERTREND /operations -----------------------------------
if [ "$LOCAL" = "1" ]; then
    if ! curl -sf -m 15 "http://localhost/api/supertrend/operations" \
            > "$TMP_SUP" 2>/dev/null; then
        [ "$QUIET" = "0" ] && echo "✗ /operations unreachable on localhost"
        exit 1
    fi
else
    if ! ssh -o ConnectTimeout=10 "$VPS_HOST" \
            "curl -sf -m 15 http://localhost/api/supertrend/operations" \
            > "$TMP_SUP" 2>/dev/null; then
        [ "$QUIET" = "0" ] && echo "✗ /operations unreachable via $VPS_HOST"
        exit 1
    fi
fi

# -- Fetch SHADOW /signal-health (best-effort) ----------------------
SHADOW_ARG=""
if [ "$LOCAL" = "1" ]; then
    if curl -sf -m 15 "http://localhost/api/smart-money/signal-health" \
            > "$TMP_SHA" 2>/dev/null; then
        SHADOW_ARG="$TMP_SHA"
    fi
else
    if ssh -o ConnectTimeout=10 "$VPS_HOST" \
            "curl -sf -m 15 http://localhost/api/smart-money/signal-health" \
            > "$TMP_SHA" 2>/dev/null; then
        SHADOW_ARG="$TMP_SHA"
    fi
fi

# -- Evaluate via Python core ---------------------------------------
QUIET_ARG=""
if [ "$QUIET" = "1" ]; then QUIET_ARG="--quiet"; fi

python3 "$PROJECT_DIR/scripts/health_check_core.py" \
    "$TMP_SUP" $SHADOW_ARG $QUIET_ARG
