# R91 — Quality Gate Env Overrides + Polymarket Cron Regression Fix (2026-04-25)

## TL;DR

Two parallel actions this round:

1. **Polymarket scan cadence regression FIXED** — `scripts/polymarket_pipeline.sh`
   was overriding `pipeline.py` with hardcoded `--markets-limit 20 --wallets-cap 30`
   (the pre-1.5c.4 values). 1.5c.4 (commit `d53ebfd`) bumped pipeline defaults
   to 60/60 to address the 0/2028 wallet pool issue, but the cron wrapper silently
   undid it. Wrapper defaults now match pipeline.py.

2. **Three new SUPERTREND env overrides shipped** — code-only, no behaviour change
   at default values. Each addresses one of R66 telemetry's next-biggest entry
   blockers after vol_mult (which R89 handled). A/B backtest values pending VPS
   access.

---

## Action 1 — Polymarket cron regression

### Symptom
User noticed: 「掃秒速度跟次數降低了」 + dashboard showed 0 of 2028 wallets
qualified for any tier (A/B/C).

### Root cause
The 1.5c.4 commit (`d53ebfd`, 2026-04-23) bumped `pipeline.py` constants:
```
ACTIVE_MARKETS_PER_RUN:    20 → 60
WALLET_COMPUTE_CAP_PER_RUN: 30 → 60
CANDIDATE_LOOKBACK_HOURS:  72 → 168 (7d)
```

But `polymarket_pipeline.sh` (the cron wrapper) defaults still read:
```bash
POLY_MARKETS_LIMIT="${POLY_MARKETS_LIMIT:-20}"
POLY_WALLETS_CAP="${POLY_WALLETS_CAP:-30}"
```

These get passed as CLI flags `--markets-limit 20 --wallets-cap 30` — which
override the new pipeline.py defaults.

`CANDIDATE_LOOKBACK_HOURS` had no CLI flag so it correctly took effect.

### Fix
Wrapper defaults bumped to 60/60 in this commit. VPS will pick up next time
the script is redeployed (or operator can also `export POLY_MARKETS_LIMIT=60`
in `/opt/ambmh/.env` for immediate effect without redeploy).

### Diagnosis of 0/2028 (separate from regression)
Tier C threshold is already permissive: 10 trades / 50% WR / $2000 PNL / $100 avg.
The `/api/polymarket/paper-trades/follower-health` endpoint already exposes
`tier_distribution` + `near_miss` to distinguish between:
- "threshold too strict" (many wallets close to but not meeting C)
- "pool quality too low" (few wallets even approach C)

Operator should hit this endpoint after the next pipeline cycle (~5min) to see
which class dominates. The 1.5c.4 expansion (60 markets × 7d lookback) was the
intended fix for "real whales not in pool"; with the wrapper regression now
fixed, the experiment can finally run as designed.

---

## Action 2 — Three new SUPERTREND env overrides

### Background
R66 telemetry across 24h sample identified entry blockers:
```
vol<=1.2*ma           207   ← R89 addressed (SUPERTREND_VOL_MULT)
quality<=0.5          174   ← R91 candidate
atr_not_rising        162   ← R91 candidate
adx<=25               141   ← R91 candidate
```

After R89 (8/8 wins, 8 trades / 6 months) the strategy is robust but very low
frequency. To reach the operator's target (~5+ trades/month/17 pairs) without
degrading WR, we need to selectively loosen the next-biggest blockers — but
only after backtest evidence shows each loosening doesn't hurt.

### Env overrides shipped (code-only this round)

```python
# strategies/supertrend.py populate_entry_trend()
SUPERTREND_QUALITY_MIN          # default 0.5  — try 0.4 first
SUPERTREND_ADX_MIN              # default 25   — try 20 first
SUPERTREND_REQUIRE_ATR_RISING   # default 1    — try "0" to disable gate
```

All three default to current R89 behaviour. Invalid values fall back to default
(no crash). 11 new tests added in `tests/test_supertrend_quality_gates.py`,
covering boundary conditions + invalid env handling.

### Pending — A/B backtest matrix

Run on R89 baseline config + each one knob loosened, on the same 6-month window:
```
20251001-20260330   strategy=SupertrendStrategy   timeframe=15m
SUPERTREND_DISABLE_CONFIRMED=1
SUPERTREND_KELLY_MODE=three_stage_inverted
SUPERTREND_VOL_MULT=1.0
+ {SUPERTREND_QUALITY_MIN=0.4 | SUPERTREND_ADX_MIN=20 | SUPERTREND_REQUIRE_ATR_RISING=0}
```

Acceptance bar (per knob, vs R89 8/8 baseline):
- WR ≥ 87.5% (one loss tolerated if frequency >= 16 trades)
- P&L > +$5.32 (the R89 absolute number)
- Max DD ≤ 1%

If two knobs pass independently, run them combined. If a knob FAILS the bar,
keep its env override at the safe default — overrides are explicit; the prod
default doesn't change unless the operator opts in.

### Reproduce (when VPS access available)

```bash
ssh root@VPS
for knob_pair in \
  "SUPERTREND_QUALITY_MIN=0.4" \
  "SUPERTREND_ADX_MIN=20" \
  "SUPERTREND_REQUIRE_ATR_RISING=0"; do
  docker exec \
    -e SUPERTREND_DISABLE_CONFIRMED=1 \
    -e SUPERTREND_KELLY_MODE=three_stage_inverted \
    -e SUPERTREND_VOL_MULT=1.0 \
    -e $knob_pair \
    ambmh-freqtrade-1 freqtrade backtesting \
    --strategy SupertrendStrategy --timeframe 15m \
    --timerange 20251001-20260330 \
    -c /freqtrade/config/config_dry.json \
    -c /freqtrade/config/config_backtest.json \
    --strategy-path /freqtrade/user_data/strategies
done
```

---

## Test results

```
tests/test_supertrend_quality_gates.py ........... [11/11 pass]
tests/test_supertrend_vol_mult.py       .......    [ 7/7  pass — no R89 regression]
```

## Files touched

- `scripts/polymarket_pipeline.sh` — defaults 20/30 → 60/60 + comment refs 1.5c.4
- `strategies/supertrend.py` — 3 new env reads in `populate_entry_trend()`,
  identical pattern to R89's `SUPERTREND_VOL_MULT`
- `tests/test_supertrend_quality_gates.py` — new (11 tests)
- `docs/reports/r91_quality_gates_design.md` — this file

## Production launch update

R89 deploy already live since 2026-04-25. R91 ships the *capability* to tune
further but does not change behaviour at default. No production change without
operator opt-in via env vars.
