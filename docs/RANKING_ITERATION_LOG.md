# Ranking Algorithm Iteration Log

Tracks data-driven tuning of `smart_money/ranking/scorer.py` + `config.py`.
Each iteration documents: hypothesis → change → result → decision.

---

## Data snapshot (2026-04-20)

- **Seeds**: 75 addresses from HL public leaderboard (mix of allTime/month/week × pnl/roi)
- **Backfill**: 365 days, 1,138,034 total trades across 75 wallets
- **Distribution**: bimodal — 45 wallets have 10k-50k trades (bot-like), 16 have <500 (inactive)

---

## Iteration 1 (baseline defaults)

**Config:**
- `min_sample_size=50, min_avg_holding_seconds=300`
- weights: sortino 0.25, PF 0.20, DD 0.15, cv 0.10, regime 0.15, martingale -0.20
- Sortino norm clamp ±3, PF log-scale to 10

**Result:** 32/75 wallets passed filters, top score **0.8180**

**Top 5:**
| # | Addr | score | note |
|---|---|---|---|
| 1 | `0x6355...ee58e` | 0.8180 | healthy: Sortino 1.83, PF 10.84, cv 1.81 |
| 2 | `0x0153...738052` | 0.7679 | healthy: Sortino 1.11, PF 15.8 |
| 3 | `0x8e09...d70c9` | 0.7608 | DD_rec=0 (unrecovered DD) but PnL $15.6M |
| 4 | `0xcac1...82b00` | 0.7469 | **grid bot**: cv=0.05, Sortino 27.58, PF 186 |
| 5 | `0xeee0...70b53` | 0.6814 | 126 samples + cv=0 (bot + small) |

**Diagnosis:** Sortino and PF clamps too loose — grid bot's extreme values
(Sortino 27, PF 186) normalize to 1.0, dominating scores. bell-shape cv
norm gives bot 0 positive, but doesn't *penalize*, so bot still placed #4.

---

## Iteration 2 (bot penalty fix)

**Hypothesis:** explicit `bot_penalty` deduction (like martingale) + tighter
Sortino/PF caps will push bots out of top ranks without hurting real traders.

**Changes:**
- `min_sample_size: 50 → 100` (kill small-sample overfits)
- `min_avg_holding_seconds: 300 → 600` (10 min, tighter HFT filter)
- `norm_sortino` clamp: `±3 → ±2` (real traders rarely exceed 2)
- `norm_profit_factor` log-scale: `10 → 5` (PF>5 is bot territory)
- new `bot_penalty(cv)`: linear ramp, cv<0.5 deducts up to 1.0
- added `w_bot_penalty=0.15`; shifted weights slightly:
  sortino 0.25→0.22, PF 0.20→0.18, cv 0.10→0.15
- new `cli/rank.py` pre-filter: skip wallets with >30k total trades (HFT upper-bound)

**Result:** 26/54 candidates eligible, top score **0.8709**, bot dropped to #11

**Top 5:**
| # | Addr | score | Δ vs iter 1 | note |
|---|---|---|---|---|
| 1 | `0x6355...ee58e` | **0.8709** | +0.0529 ↑ | same healthy leader, cv 1.81 now rewarded more |
| 2 | `0x0153...738052` | **0.8154** | +0.0475 ↑ | same healthy #2 |
| 3 | `0x0b91...a4db` | 0.7631 | new | full-balance cv 1.46 |
| 4 | `0x985f...501f` | 0.7349 | + | PnL $3M, cv 0.74 |
| 5 | `0x63a0...401d` | 0.7137 | + | |
| ... | | | | |
| **11** | `0xcac1...82b00` (iter1 #4 bot) | **0.6239** | -0.1230 ↓ | bot_penalty=0.90 bite |

**Decision:** ✅ Accept iter 2. Score target met (0.87 > 0.80) AND bots are
demonstrably demoted (not just a cherry-picked weight tweak).

---

## Walk-forward Gate (defense A) — Iteration 2

**Setup:** 3 rolling cutoffs (8mo/6mo/4mo ago), forward 120 days each,
top 10 per side, compare algo vs naive (raw pre-cutoff PnL).

**Result:** ❌ **0/3 cutoffs passed**

| cutoff | eligible | algo_median | naive_median | edge | blowup |
|---|---|---|---|---|---|
| 2025-08-22 | 6  | -$13,154 | -$13,154 | $0 | 50% |
| 2025-10-21 | 11 | -$6 | -$1,991 | +$1,985 | 40% |
| 2025-12-20 | 13 | +$991 | +$10,303 | -$9,312 | 40% |

**Diagnosis:**
- Pre-cutoff eligible pool is **6-13 wallets** — top 10 essentially = whole pool,
  so algo/naive rankings heavily overlap → edge ≈ 0.
- Backfill of only 365 days is insufficient for wallets to accumulate the
  min_sample_size=100 trades by the earlier cutoffs.
- 40-50% forward blowup rate means even "ranked-good" wallets frequently go
  bust in the next 120 days — either data too noisy or ranking not yet
  capturing tail risk.

**Decision:** **Do NOT tune params to force the gate to pass** (overfit risk).
Instead, two pre-requisites before re-running gate:
1. Expand seed pool to 200-300 addresses (re-run `cli.seed` with broader
   strategy buckets) — current 75 → too narrow.
2. Increase backfill to 540-730 days (need more pre-cutoff history).

Until both are done, gate result is **inconclusive** rather than conclusive.

---

## What's committed in iter 2

- `smart_money/config.py` — new thresholds + weights, new `w_bot_penalty`
- `smart_money/ranking/scorer.py` — `bot_penalty()`, tighter `norm_sortino`,
  tighter `norm_profit_factor`, final-score shift includes new penalty
- `smart_money/cli/rank.py` — HFT pre-filter by `count_trades` (perf fix)
- `tests/test_smart_money_ranking.py` — updated breakdown formula test
- `tests/test_smart_money_backtest.py` — updated weight-change assertion

---

## Known issue (perf)

`PostgresStore.get_trades` opens a new connection per call; with 75 wallets
× tens of thousands of rows, the stock `cli.rank` can hang on pgbouncer.
Workaround: `cli.rank` now pre-filters by `count_trades` (cheap) to skip
HFT bots before heavy fetch. For production, the fetch loop should be
replaced with a single `WHERE wallet_id IN (...)` bulk query — tracked as
tech debt, not blocking.
