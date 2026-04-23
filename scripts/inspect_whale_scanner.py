"""Quick read-only check of polymarket whale scanner state. Runs inside the
telegram-bot container which has /app/data/polymarket.db mounted via the
trade-data volume."""
import json
import sqlite3

DB = "/app/data/polymarket.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row


def section(title: str):
    print()
    print(f"=== {title} ===")


section("whale_stats tier 分布")
for r in c.execute("SELECT tier, COUNT(*) AS n FROM whale_stats GROUP BY tier ORDER BY n DESC"):
    print(f"  {r['tier']:<12} {r['n']}")

section("wallet_profiles 累積")
for r in c.execute(
    "SELECT scanner_version, COUNT(*) AS profiles, "
    "COUNT(DISTINCT wallet_address) AS wallets, "
    "MIN(scanned_at) AS first, MAX(scanned_at) AS last "
    "FROM wallet_profiles GROUP BY scanner_version"
):
    print(f"  {r['scanner_version']}: {r['profiles']} profiles / {r['wallets']} wallets ({r['first']} ~ {r['last']})")

section("最新 5 筆層級變動")
for r in c.execute(
    "SELECT wallet_address, from_tier, to_tier, reason, changed_at "
    "FROM whale_tier_history ORDER BY id DESC LIMIT 5"
):
    print(f"  {r['wallet_address'][:14]}... {r['from_tier']} → {r['to_tier']} ({r['reason']}) @ {r['changed_at']}")

section("推播紀錄 (whale_trade_alerts)")
total = c.execute("SELECT COUNT(*) FROM whale_trade_alerts").fetchone()[0]
print(f"  total: {total}")
for r in c.execute(
    "SELECT wallet_address, tier, side, outcome, notional, market_question, match_time, alerted_at "
    "FROM whale_trade_alerts ORDER BY alerted_at DESC LIMIT 10"
):
    q = (r["market_question"] or "")[:60]
    print(
        f"  [{r['tier']}] {r['wallet_address'][:10]}... "
        f"{r['side']} {r['outcome']} ${r['notional']:.0f} | {q}"
    )
    print(f"     trade time: {r['match_time']}  alerted: {r['alerted_at']}")

section("近 24h 鯨魚交易（trades 表 vs alerts 表）")
trades_24h = c.execute(
    "SELECT COUNT(*) FROM trades WHERE match_time >= datetime('now', '-24 hours')"
).fetchone()[0]
alerts_24h = c.execute(
    "SELECT COUNT(*) FROM whale_trade_alerts WHERE alerted_at >= datetime('now', '-24 hours')"
).fetchone()[0]
print(f"  trades 24h: {trades_24h}")
print(f"  alerts 24h: {alerts_24h}")

section("最近 5 個被 scanner 評估的錢包（含 features confidence）")
for r in c.execute(
    "SELECT wallet_address, tier, scanned_at, features_json "
    "FROM wallet_profiles ORDER BY scanned_at DESC LIMIT 5"
):
    feats = json.loads(r["features_json"] or "{}")
    confs = {k: v.get("confidence") for k, v in feats.items()}
    print(f"  {r['wallet_address'][:12]}... {r['tier']:<10} {r['scanned_at']}  confs={confs}")

section("scanner 看過的所有 wallet (依最近 scan 排序，含 stats)")
for r in c.execute(
    "SELECT wallet_address, tier, trade_count_90d, resolved_count, win_rate, "
    "cumulative_pnl, avg_trade_size, last_computed_at "
    "FROM whale_stats "
    "WHERE tier IN ('A','B','C','volatile') "
    "ORDER BY cumulative_pnl DESC LIMIT 10"
):
    print(
        f"  [{r['tier']:<8}] {r['wallet_address'][:10]}... "
        f"trades={r['trade_count_90d']:>3} resolved={r['resolved_count']:>2} "
        f"win={r['win_rate']*100:>5.1f}% pnl=${r['cumulative_pnl']:+,.0f} avg=${r['avg_trade_size']:,.0f}"
    )
