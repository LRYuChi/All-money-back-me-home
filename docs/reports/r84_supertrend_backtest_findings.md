# R84 — SUPERTREND Backtest Findings (2026-04-26)

## TL;DR — DO NOT GO LIVE

After 36 rounds of strategy hardening (R47–R83), **first empirical
backtest shows SUPERTREND is NET LOSING in 6-month historical sample**.

```
Window:        2025-10-01 → 2026-03-30 (6 months)
Pairs:         BTC, ETH, SOL, XRP, ADA (5 majors via StaticPairList)
Config:        config_dry.json + config_backtest.json (R84)
Strategy:      SupertrendStrategy (current main: commit e3b7a4c)

Results:
  Trades:      36
  Win Rate:    55.6%  (20W / 16L)
  Avg Profit:  -0.66% per trade
  Total P&L:   -$13.46 (-1.35% on $1000 starting balance)
  Sharpe:      -0.85   ← negative
  Profit Factor: 0.37  ← << 1.0
  Max Drawdown: 1.55%
  Avg Hold:    13:05:00 hours
```

## Interpretation

Win rate 55.6% **looks acceptable** in isolation, but Profit Factor 0.37
reveals the asymmetry: **average loser is ~2.7× the average winner**.

Math:
  - Total winners: $X
  - Total losers: $X / 0.37 ≈ $2.70 × X
  - Net: $X − $2.70X = -$1.70X (LOSS regardless of WR)

This means SUPERTREND lets winners run too short and losers run too long —
likely the trailing-stop logic (R47 4-phase trailing) cuts profitable
trades early but the initial -5% SL gives losers room to chew through
the buffer.

## Why hadn't we noticed before?

- All 36 prior rounds focused on: monitoring, alerts, telemetry,
  observability tooling
- ZERO rounds did empirical PnL validation
- Production has been 0 trades since deploy because vol filter was so
  strict — we had no live trades to see this asymmetry
- Force entry validation (R79) proved the chain WORKS but not that
  it's PROFITABLE

## Caveats

1. Backtest used StaticPairList of 5 majors. Production runs dynamic
   top-30 — different universe could show different stats.
2. R57/R58 alpha filters (FR / orderbook / correlation) were OFF (env
   vars unset in backtest run) — they MIGHT improve PF if enabled.
3. R66 evaluation telemetry not captured in backtest (lots of skips
   means we couldn't reproduce the full filter cascade).
4. Window includes both trending and chop periods — strategy might
   perform very differently in each (would need regime-segmented
   backtest to see).

## Recommendations

**Do NOT flip `SUPERTREND_LIVE=1` in production yet.**

Before live deploy, MUST address one of:

### Option A — Tune for win/loss asymmetry
- Tighten initial SL from -5% to -3% to cap loser size
- Loosen trailing exit thresholds to let winners run longer
- Re-backtest, target Profit Factor ≥ 1.3

### Option B — Enable alpha filters + re-backtest
- `SUPERTREND_FR_ALPHA=1` + `SUPERTREND_ORDERBOOK_CONFIRM=1` + 
  `SUPERTREND_CORRELATION_FILTER=1`
- Re-run R84 backtest with these env vars set
- If PF improves above 1.3, deploy with these enabled

### Option C — Switch to MeanReversionStrategy (R67)
- R67 (chop hedge) has never been backtested either
- Run `bash scripts/backtest_mr.sh 180` once OHLCV is available
- If R67 shows PF ≥ 1.3, deploy MR as primary or alongside SUPERTREND

### Option D — Reduce max_open_trades + concentrate capital
- 5 pairs × 3 max_open_trades = thin per-trade edge
- Reduce to 1-2 max_open_trades on highest-conviction signals only
- Re-backtest

## Next Steps

R85 candidates:
1. Re-run R84 with alpha filters ON
2. Re-run R84 with -3% SL (Option A)
3. Update CLAUDE.md to flag "DO NOT GO LIVE without R84 follow-up"

## Reproduce

```bash
ssh root@VPS
docker exec ambmh-freqtrade-1 freqtrade backtesting \
  --strategy SupertrendStrategy --timeframe 15m \
  --timerange 20251001-20260330 \
  -c /freqtrade/config/config_dry.json \
  -c /freqtrade/config/config_backtest.json \
  --strategy-path /freqtrade/user_data/strategies
```

Full output saved at `docs/reports/r84_supertrend_backtest_20260426.log`.
