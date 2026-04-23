"""Re-classify the 2 volatile wallets under new emerging tier logic + check impact."""
import sqlite3
import sys
sys.path.insert(0, "/app")

from polymarket.features.whales import classify_tier, WhaleStats

DB = "/app/data/polymarket.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

wallets = (
    "0x204f72f35326db932158cba6adff0b9a1da95e14",
    "0xc02147dee42356b7a4edbb1c35ac4ffa95f61fa8",
)

print("=== 現有 DB 中 tier (來自舊邏輯的 cache) ===")
for r in c.execute(
    "SELECT wallet_address, tier, trade_count_90d, win_rate, cumulative_pnl, "
    "avg_trade_size, resolved_count, segment_win_rates, last_computed_at "
    "FROM whale_stats WHERE wallet_address IN (?, ?)",
    wallets,
):
    print(f"  {r['wallet_address'][:16]}...")
    print(f"    tier (cached): {r['tier']}")
    print(f"    trades={r['trade_count_90d']} resolved={r['resolved_count']} "
          f"wr={r['win_rate']*100:.1f}% pnl=${r['cumulative_pnl']:+,.0f}")
    print(f"    last_computed_at: {r['last_computed_at']}")

    # 重新用新邏輯 classify
    import json
    seg = json.loads(r["segment_win_rates"]) if r["segment_win_rates"] else []
    stats = WhaleStats(
        wallet_address=r["wallet_address"],
        trade_count_90d=r["trade_count_90d"],
        win_rate=r["win_rate"],
        cumulative_pnl=r["cumulative_pnl"],
        avg_trade_size=r["avg_trade_size"],
        resolved_count=r["resolved_count"],
        segment_win_rates=seg,
    )
    new_tier = classify_tier(stats)
    print(f"    tier (new logic): {new_tier}")
    print(f"    stability_pass: {stats.stability_pass}")
    print()

print("=== 總結 ===")
print("現有 cache 是舊邏輯產出的；下次 cache 過期（24h）後，pipeline 會用新邏輯重分類。")
print("如要立即重算，可手動清 whale_stats 的 last_computed_at 讓 pipeline 下次重抓。")
