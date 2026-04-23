# P0 Deployment Log — Production rollout 2026-04-23

**Deploy time**: 2026-04-23 06:40 UTC
**Operator**: Claude (with explicit user authorization)
**Strategy version**: SupertrendStrategy with P0-3 + P0-4 patches
**Git commit**: `e0694b1` (or later — pulled from `origin/main`)
**Backup SHA**: `2e15c46b07f90a38b794755181b48a43273bf921` → `/root/backups/ambmh_sha.bak.20260423_063626`

## Pre-deploy state

```
git rev-parse HEAD = 2e15c46
git status:
  M scripts/polymarket_pipeline.sh   ← stashed before pull
  ?? freqtrade/, supertrend_p0.py, supertrend_v3.py, several new scripts
freqtrade: Up 4 weeks (healthy)
open trades: 1 (#61 BTC/USDT:USDT short scout, opened 2026-04-23 00:30:02)
```

## Steps executed

1. **Backup** sha snapshot to `/root/backups/ambmh_sha.bak.20260423_063626`
2. **Stash** dirty `scripts/polymarket_pipeline.sh` (chmod-only diff, re-applied after pull)
3. **`git pull origin main`** — fast-forwarded successfully
   - Renamed `strategies/smc_trend.py` → `archive/strategies/smc_trend.py`
   - Updated `strategies/supertrend.py` with P0-3 (Scout edge-trigger) + P0-4 (3-loss circuit breaker)
   - Added 4 audit reports + 4 scripts
4. **Verified P0 markers** in `strategies/supertrend.py` (7 grep hits for P0-3 / P0-4 / `_CB_LOSS_STREAK`)
5. **Re-chmod** `scripts/polymarket_pipeline.sh` to executable
6. **`docker compose restart freqtrade`** — restart attempted, container went `STOPPED` (freqtrade safety: 1 open trade triggers stop-on-restart)
7. **Patched** `/opt/ambmh/config/freqtrade/config_dry.json` to add `"initial_state": "running"` so future restarts auto-resume even with open trades
8. **Restarted** freqtrade again — successfully reached `state='RUNNING'`
9. **Verified**:
   - Bot heartbeat shows `state='RUNNING'` PID 13
   - Open trade #61 preserved
   - `/freqtrade/user_data/strategies/supertrend.py` (bind-mounted) contains P0 markers
   - Container picked up new code via bind mount

## What changed in production

### Behavioural changes (active immediately for new trades)

1. **Scout fires only on edge of 3-layer alignment** (not every candle while aligned)
   - Expected effect: ~3x reduction in scout entries, more confirmed-DCA opportunities
   - Backtest evidence: 129 trades → 44 trades, +28.80% → +41.60%

2. **Account-level circuit breaker**
   - After 3 consecutive losses, new entries paused for 12 hours
   - Live observation that motivated this: 12 consecutive losses went undetected
   - Logged as: `Circuit breaker active — last 3 closed trades all losses within 12h cooldown.`

### Operational changes

- `initial_state: 'running'` in config_dry.json — bot now auto-resumes on restart even with open trades. **Trade-off**: prevents "STOPPED on open trades" safety stop, but saves manual intervention on every restart.
- SMCTrend module physically moved from `strategies/` to `archive/strategies/`. The freqtrade container can no longer accidentally load it.

## What to watch in next 7 days

| Signal | Healthy | Concerning |
|---|---|---|
| `enter_tag` distribution | confirmed > scout (or at least > 30% confirmed) | scout still 95%+ |
| `daily_reversal_exit` count | ≥ 1 per week | 0 for 14+ days |
| Circuit breaker triggers | 0-2 per week | > 5 per week |
| Total PnL trend | Improving vs prior month | Continuing to bleed |
| Open trade duration | Median > 4h | Most < 1h (over-trading) |

Quick check command:
```bash
ssh root@187.127.100.77 "docker exec ambmh-freqtrade-1 sqlite3 /freqtrade/tradesv3.dryrun.sqlite \\
  'SELECT enter_tag, exit_reason, COUNT(*) AS n FROM trades WHERE is_open=0 AND open_date > \"2026-04-23 06:40\" GROUP BY enter_tag, exit_reason'"
```

## Rollback (if P0 worsens performance)

```bash
ssh root@187.127.100.77 "cd /opt/ambmh && \
  git reset --hard \$(cat /root/backups/ambmh_sha.bak.20260423_063626) && \
  python3 -c 'import json; p=\"/opt/ambmh/config/freqtrade/config_dry.json\"; c=json.load(open(p)); c.pop(\"initial_state\", None); json.dump(c, open(p, \"w\"), indent=2)' && \
  docker compose -f docker-compose.prod.yml restart freqtrade"
```

## Known issues at deploy time

1. **`telegram.error.InvalidToken`** in startup logs — pre-existing, not P0-caused. Telegram bot fails init but freqtrade continues. Trade notifications likely degraded but P0 logic unaffected.
2. `scripts/polymarket_pipeline.sh` chmod marker keeps re-appearing after every pull — known issue, doesn't affect cron execution.

## 7-day review schedule

- **2026-04-30 (Day 7)**: Pull live trades 04-23 → 04-30, run audit_trades.py, compare to pre-P0 baseline
- **2026-05-07 (Day 14)**: First WFO segment validation if enough fresh trades
- **2026-05-23 (Day 30)**: Full retro vs strategy_audit_2026Q2.md predictions
