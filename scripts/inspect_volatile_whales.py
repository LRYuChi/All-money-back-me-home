"""Investigate why specific wallets are tagged 'volatile' instead of A/B/C."""
import json
import sqlite3

DB = "/app/data/polymarket.db"
c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

# pre_registered thresholds for context
print("=== Pre-registered tier thresholds ===")
print("  A: trades>=20 win>=60% pnl>=10000 avg>=500")
print("  B: trades>=15 win>=55% pnl>=5000 avg>=250")
print("  C: trades>=10 win>=50% pnl>=2000 avg>=100")
print("  穩定性: 每段 30d 勝率 >= tier_win × 0.85")
print()

print("=== Volatile wallets full profile ===")
for r in c.execute(
    "SELECT * FROM whale_stats WHERE tier='volatile' ORDER BY cumulative_pnl DESC"
):
    seg = json.loads(r["segment_win_rates"]) if r["segment_win_rates"] else []
    print()
    print(f"--- {r['wallet_address']} ---")
    print(f"  trade_count_90d: {r['trade_count_90d']}")
    print(f"  resolved_count:  {r['resolved_count']}")
    print(f"  win_rate (overall): {r['win_rate']*100:.1f}%")
    print(f"  cumulative_pnl: ${r['cumulative_pnl']:+,.2f}")
    print(f"  avg_trade_size: ${r['avg_trade_size']:,.2f}")
    print(f"  segment_win_rates (3 × 30d): {seg}")
    print(f"  stability_pass: {bool(r['stability_pass'])}")
    print(f"  last_trade_at: {r['last_trade_at']}")

    # Diagnose which tier he could pass on raw stats
    # tier A
    if r['trade_count_90d']>=20 and r['win_rate']>=0.60 and r['cumulative_pnl']>=10000 and r['avg_trade_size']>=500:
        target = 'A'
    elif r['trade_count_90d']>=15 and r['win_rate']>=0.55 and r['cumulative_pnl']>=5000 and r['avg_trade_size']>=250:
        target = 'B'
    elif r['trade_count_90d']>=10 and r['win_rate']>=0.50 and r['cumulative_pnl']>=2000 and r['avg_trade_size']>=100:
        target = 'C'
    else:
        target = 'excluded'
    print(f"  [diagnosis] would qualify for tier: {target}")

    if target in ("A", "B", "C"):
        thresh = {"A": 0.60, "B": 0.55, "C": 0.50}[target]
        min_seg = thresh * 0.85
        print(f"  [diagnosis] needs each segment >= {min_seg*100:.1f}% (tier {target} × 0.85)")
        for i, s in enumerate(seg):
            status = "✓" if s >= min_seg else ("✗ insufficient samples (-1)" if s == -1 else f"✗ below threshold")
            print(f"    seg {i}: {s*100 if s!=-1 else 'N/A':<10}{'%' if s!=-1 else ''}  {status}")

# Recent whale trades that should have triggered alerts
print()
print("=== These wallets' recent trades (would-be alerts if not volatile) ===")
for waddr in [r["wallet_address"] for r in c.execute("SELECT wallet_address FROM whale_stats WHERE tier='volatile'")]:
    print()
    print(f"--- trades by {waddr[:16]}... in last 48h ---")
    for r in c.execute(
        "SELECT id, condition_id, side, size, price, notional, match_time "
        "FROM trades WHERE (taker_address=? OR maker_address=?) "
        "AND match_time >= datetime('now', '-48 hours') "
        "ORDER BY match_time DESC LIMIT 10",
        (waddr, waddr),
    ):
        # Try to find market question
        m = c.execute("SELECT question FROM markets WHERE condition_id=?", (r["condition_id"],)).fetchone()
        q = m["question"][:50] if m else "(unknown market)"
        print(
            f"  {r['match_time']}  {r['side']} ${r['notional']:.0f} ({r['size']:.2f}@{r['price']:.4f})  → {q}"
        )

# Look at recently resolved trades to estimate live performance
print()
print("=== If we lowered stability filter ratio from 0.85 to 0.70, how many would pass? ===")
qualified = 0
for r in c.execute("SELECT * FROM whale_stats WHERE tier='volatile'"):
    seg = json.loads(r["segment_win_rates"]) if r["segment_win_rates"] else []
    # determine tier qualification
    if r['trade_count_90d']>=20 and r['win_rate']>=0.60 and r['cumulative_pnl']>=10000 and r['avg_trade_size']>=500:
        thresh = 0.60
    elif r['trade_count_90d']>=15 and r['win_rate']>=0.55 and r['cumulative_pnl']>=5000 and r['avg_trade_size']>=250:
        thresh = 0.55
    elif r['trade_count_90d']>=10 and r['win_rate']>=0.50 and r['cumulative_pnl']>=2000 and r['avg_trade_size']>=100:
        thresh = 0.50
    else:
        continue
    relaxed = thresh * 0.70
    if all(s >= relaxed for s in seg if s != -1) and len([s for s in seg if s != -1]) >= 2:
        qualified += 1
        print(f"  would qualify: {r['wallet_address'][:14]}... at relaxed {relaxed*100:.0f}% per segment")
total_volatile = c.execute("SELECT COUNT(*) FROM whale_stats WHERE tier='volatile'").fetchone()[0]
print(f"  total relaxed-qualified: {qualified} / {total_volatile} volatile")
