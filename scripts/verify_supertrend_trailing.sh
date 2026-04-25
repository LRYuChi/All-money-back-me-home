#!/bin/bash
# ============================================================
# Verify SupertrendStrategy round 47 trailing fix actually helps PF
# ============================================================
# Runs two backtests:
#   1. With current code (use_custom_stoploss=True)  ← round 47
#   2. With use_custom_stoploss patched to False     ← previous behavior
# Compares profit factor + max drawdown to confirm the trailing
# logic actually improves performance vs. the static -5% SL.
#
# Usage:
#   ./scripts/verify_supertrend_trailing.sh                # default 200 days
#   ./scripts/verify_supertrend_trailing.sh 20251001-20260425
#
# Output: comparison table + paths to detailed reports
# Exit codes:
#   0 — both backtests ran (regardless of which is better)
#   1 — backtest infra failure
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

TIMERANGE="${1:-20251001-20260425}"
TF="${TF:-15m}"
OUTDIR="data/reports/trailing_verify_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

source .venv/bin/activate

STRATEGY_FILE="strategies/supertrend.py"
BACKUP="$OUTDIR/supertrend.py.bak"
cp "$STRATEGY_FILE" "$BACKUP"

run_backtest() {
    local label="$1"
    local outfile="$OUTDIR/$label.json"
    local logfile="$OUTDIR/$label.log"
    echo ""
    echo "=== Backtest: $label ==="
    freqtrade backtesting \
      --strategy SupertrendStrategy \
      --timeframe "$TF" \
      --timerange "$TIMERANGE" \
      -c config/freqtrade/config_dry.json \
      --strategy-path strategies/ \
      --export trades \
      --export-filename "$outfile" \
      2>&1 | tee "$logfile"
}

restore() {
    echo "Restoring $STRATEGY_FILE from backup..."
    mv "$BACKUP" "$STRATEGY_FILE"
}
trap restore EXIT

# Run 1: current (round 47 — trailing ON)
echo "Verifying use_custom_stoploss is True in current code..."
grep -E "use_custom_stoploss = True" "$STRATEGY_FILE" >/dev/null \
    || { echo "ERROR: round 47 fix not present (use_custom_stoploss != True)"; exit 1; }
run_backtest "with_trailing"

# Run 2: patched copy with trailing OFF (mimic pre-round-47)
echo ""
echo "Patching use_custom_stoploss = False for comparison run..."
sed -i.tmp 's/use_custom_stoploss = True/use_custom_stoploss = False/' "$STRATEGY_FILE"
rm -f "$STRATEGY_FILE.tmp"
run_backtest "static_only_minus_5pct"

# Restore handled by trap
echo ""
echo "=== Comparison Summary ==="
echo "Reports saved in: $OUTDIR/"
echo ""
echo "Compare the two .log files for:"
echo "  - Total profit %"
echo "  - Profit factor"
echo "  - Max drawdown"
echo "  - Win rate"
echo "  - # winning vs losing trades"
echo ""
echo "Hypothesis: round 47 (trailing ON) should have:"
echo "  • Higher win rate (locking profit instead of round-trip losses)"
echo "  • Higher profit factor"
echo "  • Lower max drawdown"
echo "  • Possibly lower avg-winning-trade size (we cap winners early)"
echo ""
echo "If trailing UNDERPERFORMS static -5% in this window:"
echo "  - Trailing thresholds (p1/p2/p3) may need tuning per regime"
echo "  - The 1.5%/3.0%/6.0% phases might be too tight for current vol"
echo "  - Run backtest-analysis for per-trade detail:"
echo "      freqtrade backtesting-analysis --analysis-groups 0,1,2,3"
