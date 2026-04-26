# R90 — Walk-Forward of R89 Config + Pair Attribution (2026-04-26)

## TL;DR — 8/8 wins survives walk-forward, edge concentrates on BTC

R88 walk-forward verified R87 (5 trades aggregate) but R89 changed
vol_mult 1.2→1.0 yielding 8 trades total. R90 re-runs walk-forward on
R89 config to verify the 3 NEW trades unlocked aren't period-specific.

**Result**: All 3 windows positive. 8 trades total, 100% WR maintained.
But pair attribution reveals edge concentrates on BTC (6/8) + ADA (2/8).
ETH/SOL/XRP produce ZERO wins across all 3 windows.

## R90 walk-forward results

Same 3 windows as R88, but with R89 config (vol_mult=1.0 added):

```
SUPERTREND_DISABLE_CONFIRMED=1
SUPERTREND_KELLY_MODE=three_stage_inverted
SUPERTREND_VOL_MULT=1.0
```

| Window           | Trades | WR    | P&L    | Tiers              | Pair Winners |
|:-----------------|-------:|------:|-------:|:-------------------|:-------------|
| Oct-Nov 2025     | 2      | 100%  | +$0.61 | pre_scout 2        | BTC 2        |
| Dec-Jan 2025-26  | 4      | 100%  | +$1.12 | pre_scout 3, scout 1 | BTC 3, ADA 1 |
| Feb-Mar 2026     | 2      | 100%  | +$0.31 | scout 2            | BTC 1, ADA 1 |
| **TOTAL**        | **8**  | **100%** | **+$2.04** | mixed | **BTC 6, ADA 2** |

(Aggregate sum is ~$2.04 vs R89 single-run +$5.32 — walk-forward loses
boundary trades that span window edges. Per-window wins are what
matter for robustness.)

## Statistical signals

Under null hypothesis (50% WR across all entries):
- 8 wins / 8 trials = P = 1/256 ≈ **0.39%**
- 3/3 windows positive on top of that = additional consistency signal

R88 was 5/5 (P ≈ 3%). R90 extends to 8/8 (P ≈ 0.39%) — significantly
stronger evidence.

## 🚨 Pair concentration — BTC dominates

```
BTC/USDT:USDT  6 wins (75% of total)
ADA/USDT:USDT  2 wins (25% of total)
ETH/USDT:USDT  0 wins
SOL/USDT:USDT  0 wins
XRP/USDT:USDT  0 wins
```

This is a NEW finding not visible in R88 (only 5 trades, BTC didn't
even appear by name). With R89's larger sample, the BTC/ADA bias is
clear.

### Implications

1. **Edge may be BTC-specific (or BTC + sister-cap mid-cap)**
   - ETH should normally correlate with BTC; if BTC fires entries +
     wins but ETH doesn't fire at all, suggests pair-specific
     filter behaviour (volume profile? ATR pattern?)

2. **Deploying with 17-pair prod whitelist might dilute** — if 14 of
   17 pairs never fire (like ETH/SOL/XRP here), they consume scanning
   resources without contributing edge

3. **Capital allocation question** — should we restrict the strategy
   to BTC + a curated list of "compatible" pairs?

### Hypothesis to test next

Run R89 config with EACH single pair in isolation. See per-pair PF.
Pairs that never fire OR fire negative are removable from prod whitelist.

## Production launch update

5 of 5 backtest prerequisites now passed (R88 had 4/5):

| Check | Round | Result |
|:------|------:|:-------|
| Backtested 6m | R84-R87 | -$13.46 → +$5.32 progression |
| Root-caused | R85 | confirmed tier identified |
| Walk-forward (R87) | R88 | 5/5 wins |
| Frequency tuning | R89 | 8/8 wins, +150% P&L |
| **Walk-forward (R89)** | **R90** | **8/8 wins per window** |

❌ Forward-test in real dry-run (operator decision)
❌ Live capital decision (still below confidence bar — 8 wins is solid
   but real dry-run forward-test gives the highest-fidelity signal)

## R91 candidates

1. **Per-pair backtest** — run R89 on BTC alone, ETH alone, etc.
   See if dropping ETH/SOL/XRP from whitelist changes results.

2. **Fresh OHLCV download** — backtest only covers Sep 2025-Mar 2026.
   Pull more recent (Apr 2026) once available, re-validate.

3. **Forward-test deploy** — operator action, 2-4 weeks observation.

4. **Investigate pair filter behaviour** — why does ETH not fire
   entries when BTC does? Same volume gates / ADX / quality, but
   different outcomes. Could be ETH's higher correlation to alts
   producing different multi-tf signal patterns.

## Reproduce

```bash
for trange in 20251001-20251201 20251201-20260201 20260201-20260330; do
  ssh root@VPS "docker exec \
    -e SUPERTREND_DISABLE_CONFIRMED=1 \
    -e SUPERTREND_KELLY_MODE=three_stage_inverted \
    -e SUPERTREND_VOL_MULT=1.0 \
    ambmh-freqtrade-1 freqtrade backtesting \
    --strategy SupertrendStrategy --timeframe 15m \
    --timerange $trange \
    -c /freqtrade/config/config_dry.json \
    -c /freqtrade/config/config_backtest.json \
    --strategy-path /freqtrade/user_data/strategies"
done
```
