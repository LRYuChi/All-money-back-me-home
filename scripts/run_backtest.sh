#!/bin/bash
# Quick backtest runner for All-money-back-me-home
# Usage: ./scripts/run_backtest.sh [strategy_name] [timerange]
# Example: ./scripts/run_backtest.sh AdaptiveRSI 20240601-20260317

set -e
cd "$(dirname "$0")/.."

STRATEGY="${1:-AdaptiveRSI}"
TIMERANGE="${2:-20240317-20260317}"

source .venv/bin/activate

echo "=== Running backtest ==="
echo "Strategy: $STRATEGY"
echo "Timerange: $TIMERANGE"
echo ""

freqtrade backtesting \
  --strategy "$STRATEGY" \
  --timeframe 1h \
  --timerange "$TIMERANGE" \
  -c config/freqtrade/config_dry.json \
  -c config/freqtrade/config_secrets.json \
  --strategy-path strategies/
