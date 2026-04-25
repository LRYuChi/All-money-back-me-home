#!/bin/bash
# ============================================================
# MeanReversionStrategy Backtest — R82
# ============================================================
# Empirical validation of R67 MR strategy. Run before deploying as a
# parallel shadow container — MR was written but never validated against
# real OKX historical data.
#
# Differences vs scripts/backtest_ci.sh (R54 — SUPERTREND):
#   * --strategy MeanReversionStrategy
#   * Forces MR_ENABLED=1 (R67 master switch defaults OFF)
#   * Forces MR_REGIME_GATE=0 (regime detector needs live HL data,
#     not available in backtest — without this gate every entry is
#     blocked)
#   * Exploratory: no baseline / regression comparison. R82 is for
#     "should we deploy MR?" decision data.
#
# PREREQUISITE: OHLCV data must be downloaded for the backtest window.
# Run before first use:
#   freqtrade download-data \
#     -c config/freqtrade/config_dry.json \
#     --timeframes 15m 1h 4h 1d \
#     --days 365 \
#     -p BTC/USDT:USDT ETH/USDT:USDT  # or any subset
#
# OR (on VPS, freqtrade container has cached data):
#   docker compose exec freqtrade freqtrade download-data \
#     --timeframes 15m 1h 4h 1d --days 365
#
# Without prior download, backtest hangs at "Initializing leverage_tiers"
# because it can't fetch on-the-fly without an authenticated API key.
#
# Usage:
#   bash scripts/backtest_mr.sh                # default: 90d window
#   bash scripts/backtest_mr.sh 180            # 180d
#   bash scripts/backtest_mr.sh 30 --pair BTC/USDT:USDT
#                                              # 30d, single pair
#
# Outputs:
#   data/reports/mr_backtest/<window>d_<timestamp>.json (raw freqtrade)
#   data/reports/mr_backtest/<window>d_<timestamp>.log  (full output)
#   stdout: clean summary {n_trades, win_rate, PF, max_dd, sharpe}
#
# Exit codes:
#   0 — backtest completed (regardless of profitability)
#   1 — backtest infrastructure failure (data missing, freqtrade error)
#   2 — invalid args
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

OUTDIR="data/reports/mr_backtest"
mkdir -p "$OUTDIR"

DAYS="${1:-90}"
PAIR_OVERRIDE=""
if [ "${2:-}" = "--pair" ] && [ -n "${3:-}" ]; then
    PAIR_OVERRIDE="$3"
fi

if ! [[ "$DAYS" =~ ^[0-9]+$ ]] || [ "$DAYS" -lt 7 ]; then
    echo "ERROR: window days must be integer ≥ 7"
    exit 2
fi

END_DATE=$(date -u +%Y%m%d)
START_DATE=$(date -u -v-"${DAYS}"d +%Y%m%d 2>/dev/null \
    || date -u -d "${DAYS} days ago" +%Y%m%d)
TIMERANGE="${START_DATE}-${END_DATE}"
TS=$(date +%Y%m%d_%H%M%S)
OUTFILE="$OUTDIR/${DAYS}d_${TS}.json"
LOGFILE="$OUTDIR/${DAYS}d_${TS}.log"

echo "=== MR Backtest ${DAYS}d (${TIMERANGE}) ==="
echo "  Strategy:    MeanReversionStrategy"
echo "  MR_ENABLED:  1 (forced)"
echo "  Regime gate: 0 (forced — regime detector unavailable in backtest)"
[ -n "$PAIR_OVERRIDE" ] && echo "  Pair:        $PAIR_OVERRIDE"
echo "  Outfile:     $OUTFILE"
echo ""

# Detect freqtrade availability
FREQTRADE_CMD=""
if command -v freqtrade &> /dev/null; then
    FREQTRADE_CMD="freqtrade"
elif [ -f .venv/bin/freqtrade ]; then
    FREQTRADE_CMD=".venv/bin/freqtrade"
elif docker compose -f docker-compose.prod.yml ps freqtrade 2>/dev/null \
        | grep -q "Up"; then
    # Run via docker compose exec (uses already-running freqtrade container)
    FREQTRADE_CMD="docker compose -f docker-compose.prod.yml exec -T freqtrade freqtrade"
else
    echo "ERROR: freqtrade not found locally and no running container."
    echo "  Install: pip install freqtrade"
    echo "  Or run on VPS where docker compose freqtrade is up."
    exit 1
fi

# Build extra args
EXTRA_ARGS=""
if [ -n "$PAIR_OVERRIDE" ]; then
    EXTRA_ARGS="--pairs $PAIR_OVERRIDE"
fi

# Run backtest with MR env overrides
MR_ENABLED=1 MR_REGIME_GATE=0 $FREQTRADE_CMD backtesting \
    --strategy MeanReversionStrategy \
    --timeframe 15m \
    --timerange "$TIMERANGE" \
    -c config/freqtrade/config_dry.json \
    --strategy-path strategies/ \
    --export trades \
    --export-filename "$OUTFILE" \
    $EXTRA_ARGS \
    2>&1 | tee "$LOGFILE" || {
        echo ""
        echo "✗ Backtest infrastructure failure — see $LOGFILE"
        exit 1
    }

# Parse summary out of the freqtrade output
echo ""
echo "=== Summary ==="
python3 << PY
import json, re, sys
from pathlib import Path

with open("$LOGFILE") as f:
    text = f.read()

def grep(pat, default=None, cast=float):
    m = re.search(pat, text)
    if m is None:
        return default
    try:
        return cast(m.group(1))
    except (ValueError, IndexError):
        return default

summary = {
    "window_days": $DAYS,
    "timerange": "$TIMERANGE",
    "n_trades": grep(r"Total/Daily Avg Trades\s*\|\s*(\d+)", 0, int),
    "profit_factor": grep(r"Profit factor\s*\|\s*([\d.]+)"),
    "win_rate_pct": grep(r"Wins\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*([\d.]+)"),
    "max_drawdown_pct": grep(
        r"Max % of account underwater\s*\|\s*([\d.]+)",
    ),
    "total_profit_pct": grep(r"Total profit %\s*\|\s*([+-]?[\d.]+)"),
    "sharpe": grep(r"Sharpe\s*\|\s*([+-]?[\d.]+)"),
    "sortino": grep(r"Sortino\s*\|\s*([+-]?[\d.]+)"),
}

print(json.dumps(summary, indent=2))

# Verdict
n = summary["n_trades"] or 0
pf = summary["profit_factor"]
wr = summary["win_rate_pct"]
print()
if n == 0:
    print("⚠ ZERO TRADES — strategy never fired in this window. Check:")
    print("  - regime gate was disabled (this script forces MR_REGIME_GATE=0)")
    print("  - BB/RSI thresholds not too strict (defaults: 30/70)")
    print("  - Universe pairs have enough volatility for BB to widen")
elif pf is None:
    print("⚠ Profit factor not extractable — see raw log.")
elif pf >= 1.5 and wr and wr >= 50:
    print(f"✅ STRONG  PF={pf} WR={wr}% n={n} — consider deploy")
elif pf >= 1.2:
    print(f"✓  OK     PF={pf} n={n} — borderline; tune before deploy")
elif pf >= 1.0:
    print(f"~  EVEN   PF={pf} n={n} — break-even; do NOT deploy without tuning")
else:
    print(f"✗  LOSS   PF={pf} n={n} — strategy unprofitable in this window")
PY
