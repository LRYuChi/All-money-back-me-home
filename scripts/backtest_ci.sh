#!/bin/bash
# ============================================================
# Supertrend Backtest CI — R54
# ============================================================
# Run 3 fixed windows (90d / 180d / 365d) and write summary JSONs.
# Compares against `data/reports/backtest_ci/baseline.json` if present;
# fails (exit 1) if profit factor degrades >10% from baseline.
#
# Usage:
#   bash scripts/backtest_ci.sh                # run all windows
#   bash scripts/backtest_ci.sh 90             # just 90d
#   bash scripts/backtest_ci.sh --update-baseline   # rerun + save baseline
#
# Outputs:
#   data/reports/backtest_ci/{90d,180d,365d}_<timestamp>.json
#   data/reports/backtest_ci/latest.json (combined summary)
#
# Recommended use:
#   - Pre-commit hook: bash scripts/backtest_ci.sh 90 (fast 90d sanity)
#   - Pre-merge: bash scripts/backtest_ci.sh (all 3 windows)
#   - Post-tuning: bash scripts/backtest_ci.sh --update-baseline
#
# Exit codes:
#   0 — all windows passed (or no baseline to compare against)
#   1 — at least one window degraded > 10% from baseline
#   2 — backtest infra failure
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

OUTDIR="data/reports/backtest_ci"
mkdir -p "$OUTDIR"

UPDATE_BASELINE=false
WINDOWS=("90" "180" "365")

# Parse args
if [[ "${1:-}" == "--update-baseline" ]]; then
    UPDATE_BASELINE=true
elif [[ -n "${1:-}" && "$1" =~ ^[0-9]+$ ]]; then
    WINDOWS=("$1")
fi

# Use today as the end date. Backtest needs YYYYMMDD-YYYYMMDD format.
END_DATE=$(date -u +%Y%m%d)

source .venv/bin/activate 2>/dev/null || {
    echo "WARN: .venv not found — assuming freqtrade is on PATH"
}

run_one_window() {
    local days="$1"
    local start_date
    start_date=$(date -u -v-"${days}"d +%Y%m%d 2>/dev/null \
        || date -u -d "${days} days ago" +%Y%m%d)
    local timerange="${start_date}-${END_DATE}"
    local outfile="$OUTDIR/${days}d_$(date +%Y%m%d_%H%M%S).json"
    local logfile="$OUTDIR/${days}d_$(date +%Y%m%d_%H%M%S).log"

    echo "=== Backtest ${days}d (${timerange}) ==="
    freqtrade backtesting \
        --strategy SupertrendStrategy \
        --timeframe 15m \
        --timerange "$timerange" \
        -c config/freqtrade/config_dry.json \
        --strategy-path strategies/ \
        --export trades \
        --export-filename "$outfile" \
        2>&1 | tee "$logfile"

    # Extract PF / Sharpe / max DD / win rate from freqtrade output
    .venv/bin/python -c "
import json, re, sys
with open('$logfile') as f:
    text = f.read()

def grep(pat, default=None):
    m = re.search(pat, text)
    return float(m.group(1)) if m else default

summary = {
    'window_days': $days,
    'timerange': '$timerange',
    'profit_factor': grep(r'Profit factor\s*\|\s*([\d.]+)'),
    'win_rate_pct': grep(r'Wins\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*([\d.]+)'),
    'max_drawdown_pct': grep(r'Drawdown\s*\(absolute\)\s*\|\s*([\d.]+)'),
    'total_profit_pct': grep(r'TOTAL.*?\|\s*([+-]?[\d.]+)\s*\|\s*$'),
    'n_trades': int(grep(r'TOTAL\s*\|\s*(\d+)') or 0),
}
print(json.dumps(summary, indent=2))
with open('$outfile.summary.json', 'w') as f:
    json.dump(summary, f, indent=2)
" || echo "  (parse failed — log preserved at $logfile)"
}

declare -a SUMMARIES=()
for days in "${WINDOWS[@]}"; do
    run_one_window "$days"
    SUMMARIES+=("$OUTDIR/${days}d_*.summary.json")
done

# Combine into latest.json
.venv/bin/python -c "
import glob, json, os
windows = []
for d in [${WINDOWS[*]}]:
    files = sorted(glob.glob(f'$OUTDIR/{d}d_*.summary.json'))
    if files:
        with open(files[-1]) as f:
            windows.append(json.load(f))
out = {'windows': windows, 'generated_at': '$END_DATE'}
with open('$OUTDIR/latest.json', 'w') as f:
    json.dump(out, f, indent=2)
print('Combined → $OUTDIR/latest.json')
print(json.dumps(out, indent=2))
"

# Update baseline?
if [[ "$UPDATE_BASELINE" == "true" ]]; then
    cp "$OUTDIR/latest.json" "$OUTDIR/baseline.json"
    echo ""
    echo "✓ Baseline updated → $OUTDIR/baseline.json"
    exit 0
fi

# Compare with baseline
if [[ -f "$OUTDIR/baseline.json" ]]; then
    echo ""
    echo "=== Comparing against baseline ==="
    .venv/bin/python -c "
import json, sys

with open('$OUTDIR/baseline.json') as f:
    base = json.load(f)
with open('$OUTDIR/latest.json') as f:
    cur = json.load(f)

base_by_d = {w['window_days']: w for w in base.get('windows', [])}
degraded = []
for cw in cur.get('windows', []):
    d = cw['window_days']
    bw = base_by_d.get(d)
    if not bw:
        continue
    base_pf = bw.get('profit_factor') or 0
    cur_pf = cw.get('profit_factor') or 0
    if base_pf > 0:
        change_pct = (cur_pf - base_pf) / base_pf * 100
        marker = '⚠️' if change_pct < -10 else '✓'
        print(f'{marker} {d}d: PF {base_pf:.2f} → {cur_pf:.2f} ({change_pct:+.1f}%)')
        if change_pct < -10:
            degraded.append((d, change_pct))

if degraded:
    print()
    print(f'❌ {len(degraded)} window(s) degraded > 10%:')
    for d, change in degraded:
        print(f'   {d}d: {change:+.1f}%')
    sys.exit(1)
print()
print('✓ All windows within 10% of baseline')
"
else
    echo ""
    echo "ℹ No baseline yet (run with --update-baseline to save current as baseline)"
fi

echo ""
echo "=== Backtest CI complete ==="
