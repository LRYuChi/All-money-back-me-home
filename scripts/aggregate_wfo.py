"""Aggregate per-segment WFO backtest JSONs into a single comparison table."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean


def main(audit_dir: str) -> None:
    audit = Path(audit_dir)
    rows = []
    for d in sorted(audit.glob("wfo_seg*")):
        json_files = list(d.glob("backtest-result-*.json"))
        if not json_files:
            continue
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        s = data["strategy"][next(iter(data["strategy"]))]
        n = len(s["trades"])
        if n == 0:
            rows.append({"segment": d.name, "n": 0})
            continue
        rows.append({
            "segment": d.name,
            "period": f"{s['backtest_start'][:10]} → {s['backtest_end'][:10]}",
            "n": n,
            "pnl_pct": s.get("profit_total", 0) * 100,
            "pnl_abs": s.get("profit_total_abs", 0),
            "pf": s.get("profit_factor"),
            "sharpe": s.get("sharpe", 0),
            "wr": s["wins"] / n * 100,
            "max_dd": s.get("max_drawdown_account", 0) * 100,
            "max_consec_losses": s.get("max_consecutive_losses", 0),
            "market_change": s.get("market_change", 0) * 100,
        })

    print(f"{'Segment':<22} {'Period':<26} {'N':>4} {'PnL%':>8} {'PF':>6} {'WR%':>6} {'MaxDD%':>7} {'Mkt%':>8} {'CL':>3}")
    print("-" * 100)
    for r in rows:
        if r["n"] == 0:
            print(f"{r['segment']:<22}  no trades")
            continue
        pf = r["pf"] if r["pf"] is not None else 0
        print(
            f"{r['segment']:<22} {r['period']:<26} {r['n']:>4} "
            f"{r['pnl_pct']:>+7.2f}% {pf:>6.2f} {r['wr']:>5.1f}% "
            f"{r['max_dd']:>6.2f}% {r['market_change']:>+7.1f}% {r['max_consec_losses']:>3}"
        )

    print()
    print("--- aggregate ---")
    valid = [r for r in rows if r["n"] > 0]
    if valid:
        print(f"profitable segments: {sum(1 for r in valid if r['pnl_pct'] > 0)} / {len(valid)}")
        print(f"total PnL across segments (abs): ${sum(r['pnl_abs'] for r in valid):.2f}")
        print(f"avg PnL%: {mean(r['pnl_pct'] for r in valid):.2f}%")
        print(f"best segment: {max(valid, key=lambda r: r['pnl_pct'])['segment']} ({max(r['pnl_pct'] for r in valid):.2f}%)")
        print(f"worst segment: {min(valid, key=lambda r: r['pnl_pct'])['segment']} ({min(r['pnl_pct'] for r in valid):.2f}%)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/audit")
