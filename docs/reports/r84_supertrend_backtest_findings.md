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

---

## R85 Update (2026-04-26) — 🎯 Found the actual root cause

Re-ran the backtest twice with R57/R58 alpha filters and -3% SL — both
produced **identical** PF 0.37 / 36 trades. Confirmed:
- Alpha filters don't fire in backtest (regime detector needs live HL
  data, FR rarely extreme, correlation needs n_open ≥ 2)
- `--stoploss -0.03` config override has no effect because R47
  custom_stoploss callback overrides the class attribute

**Then broke down by enter_tag** and found the smoking gun:

```
pre_scout : 3 trades, +0.52% avg, 100% WR  ← profitable!
scout     : 2 trades, +0.31% avg, 100% WR  ← profitable!
confirmed : 31 trades, -0.84% avg, 48.4% WR ← LOSER (86% of trades)
```

The `confirmed` tier (4-tf aligned, highest conviction, R49 gives 0.85
Kelly fraction) is the SOURCE of the entire loss. The `pre_scout` and
`scout` tiers (R49 gives them 0.25 / 0.50 fractions) are perfect.

**Hypothesis**: `confirmed` entries fire AFTER full alignment which means
the trend is already mature → high reversal risk. `pre_scout` enters
during formation → captures more of the move.

R49 Kelly sizing is INVERTED relative to actual edge:
  Current:  pre_scout 0.25 / scout 0.50 / confirmed 0.85
  Suggested: pre_scout 0.85 / scout 0.50 / confirmed 0.25 (or 0)

Exit reason breakdown also shows `trailing_stop_loss` accounts for 26/36
exits (72%) with avg -0.69%. R47's 4-phase trailing might be cutting
winners too aggressively.

### R86 candidates (very actionable)

1. **Invert R49 Kelly fractions** — pre_scout big, confirmed small/off
2. **Disable confirmed tier entirely** in populate_entry_trend
3. **Loosen trailing logic** in custom_stoploss (Phase 2/3 thresholds)
4. **Re-test with extended sample** (more trades to confirm 100% WR
   on pre_scout/scout isn't pure luck from N=3)

Sample size caveat: pre_scout 100% WR on N=3 could be variance, but the
DIRECTION of the asymmetry (early-entry > late-entry) matches well-known
momentum strategy literature.

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
