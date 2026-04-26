# R87 — Disable Confirmed Tier Findings (2026-04-26)

## TL;DR — Strategy crosses to NET PROFIT (with caveats)

Per R86 docs Path 1: completely disable the `confirmed` tier in
`populate_entry_trend`. Backtest shows strategy becomes profitable for
the first time in this 3-round empirical sequence.

## Three-round progression

Same 6-month backtest (BTC/ETH/SOL/XRP/ADA, 2025-10-01 → 2026-03-30).
Each row swaps env vars; nothing else changes.

| Config                                     | Trades | Win Rate | Total P&L | PF      | Sharpe |
|:-------------------------------------------|-------:|---------:|----------:|--------:|-------:|
| R84 baseline (default Kelly)               | 36     | 55.6%    | -$13.46   | 0.37    | -0.85  |
| R86 inverted Kelly                         | 36     | 55.6%    | -$2.88    | 0.51    | -0.65  |
| **R87 disabled confirmed + inverted**      | **5**  | **100%** | **+$2.12**| **∞**   | **+0.70** |

The +$5.00 swing from R86 → R87 is purely from removing the
confirmed-tier signals (which the prior 31 trades came from).

## R87 trade detail

```
Tier         Trades  Avg Profit  Total       Win%
pre_scout    3       +0.52%      +$1.67      100%
scout        2       +0.31%      +$0.45      100%
confirmed    0       (disabled)
TOTAL        5       +0.44%      +$2.12      100%

Exit reason  Exits   Avg Profit  Total       Win%
time_decay_sideways  2       +0.64%      +$1.37      100%
trailing_stop_loss   3       +0.31%      +$0.75      100%
TOTAL                5       +0.44%      +$2.12      100%
```

100% WR across entries AND exit reasons. No drawdown observed.

## Caveats — DO NOT generalize from N=5

1. **Sample size**: 5 trades in 6 months is far too small to claim
   statistical significance. Coin-flip with 5 heads has ~3% probability
   without any edge — pre_scout/scout 100% WR could be lucky variance.

2. **Trade frequency**: 0.83 trades/month is very low. Capital is idle
   86% of the time previously occupied by confirmed entries.

3. **Market regime coverage**: Window includes specific regimes; future
   regimes might not produce the same pattern.

4. **No losers, no learning**: Without losses we can't know if the
   trailing logic / stop loss work correctly under stress.

5. **Profit factor = 0.00 / Sortino = -100**: Freqtrade's metric calc
   has degenerate behavior with zero losers (division by zero).
   Interpret as "all-positive sample" not literal values.

## Trade-off vs prior configs

| Aspect              | R84 baseline | R87 minimal |
|:--------------------|-------------:|------------:|
| Capital deployed    | ~$$$ heavy   | ~$ light    |
| Trades / month      | 6.0          | 0.83        |
| Risk per trade      | up to 0.85 K | up to 0.85 K|
| Edge per trade      | -0.66% avg   | +0.44% avg  |
| Sample variability  | LOW          | **HIGH**    |

R87 trades less but with positive expectancy. R84 trades more but with
negative expectancy. The choice is between:
  A) Many bad-EV trades (reliable losses)
  B) Few good-EV trades (statistically uncertain)

## R88 candidates

1. **Extend backtest window** — try 12 months instead of 6 to grow N
   beyond 5 trades and rule out variance
2. **Multi-pair analysis** — were all 5 winning trades on one pair? If
   so, edge might be pair-specific not strategy-wide
3. **Walk-forward analysis** — run rolling 3-month windows to see if
   pre_scout/scout edge is consistent or window-specific
4. **Forward-test in dry-run** — deploy R87 config to VPS, observe 2-4
   weeks of real signals before considering LIVE

## Production launch decision

**Still NOT recommend SUPERTREND_LIVE=1 yet.** N=5 is below any
reasonable confidence threshold. But:

- Strategy now has a CONFIGURATION that's empirically positive
- Path forward is much clearer than baseline
- Recommended: deploy with both env flags ON in DRY-RUN, observe 2 weeks

Deploy commands:
```bash
ssh root@VPS
cd /opt/ambmh
echo 'SUPERTREND_DISABLE_CONFIRMED=1' >> .env
echo 'SUPERTREND_KELLY_MODE=three_stage_inverted' >> .env
bash scripts/redeploy_service.sh freqtrade
```

Then watch `/api/supertrend/operations` for trade lifecycle.

## Reproduce

```bash
ssh root@VPS
docker exec -e SUPERTREND_DISABLE_CONFIRMED=1 \
  -e SUPERTREND_KELLY_MODE=three_stage_inverted \
  ambmh-freqtrade-1 freqtrade backtesting \
  --strategy SupertrendStrategy --timeframe 15m \
  --timerange 20251001-20260330 \
  -c /freqtrade/config/config_dry.json \
  -c /freqtrade/config/config_backtest.json \
  --strategy-path /freqtrade/user_data/strategies
```
