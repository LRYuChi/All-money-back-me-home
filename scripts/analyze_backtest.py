"""Analyse a Freqtrade backtest JSON output for the strategy audit report."""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def main(json_path: str) -> None:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    # Top-level shape
    strategy_key = next(iter(data["strategy"]))
    s = data["strategy"][strategy_key]
    trades = s["trades"]
    n = len(trades)

    print(f"=== {strategy_key}  ({n} trades) ===")
    def g(k, default=None):
        return s.get(k, default)
    print(f"period: {s['backtest_start']} ~ {s['backtest_end']}")
    print(f"market_change_pct: {g('market_change',0)*100:.1f}%")
    print(f"profit_total: {g('profit_total',0)*100:.2f}%  (abs ${g('profit_total_abs',0):.2f})")
    pf = g('profit_factor', 0)
    print(f"profit_factor: {pf:.2f}" if isinstance(pf,(int,float)) else f"profit_factor: {pf}")
    print(f"sharpe: {g('sharpe',0):.2f}  sortino: {g('sortino',0):.2f}  calmar: {g('calmar',0):.2f}")
    print(f"max_dd_pct: {g('max_drawdown_account',0)*100:.2f}%  abs ${g('max_drawdown_abs',0):.2f}")
    print(f"win_rate: {s['wins']/n*100:.1f}%  ({s['wins']}/{n})")
    print(f"max consec wins/losses: {g('max_consecutive_wins',0)}/{g('max_consecutive_losses',0)}")
    print()

    # By exit reason
    by_exit = defaultdict(list)
    for t in trades:
        by_exit[t.get("exit_reason", "?")].append(t)
    print("=== by exit reason ===")
    for k, ts in sorted(by_exit.items(), key=lambda kv: -len(kv[1])):
        ps = [t["profit_ratio"] for t in ts]
        wins = sum(1 for p in ps if p > 0)
        pnl = sum(t["profit_abs"] for t in ts)
        print(f"  {k:<28} n={len(ts):<3} pnl=${pnl:>8.2f} avg={mean(ps)*100:>6.2f}% wr={wins/len(ts)*100:>5.1f}%")

    # By entry tag
    by_tag = defaultdict(list)
    for t in trades:
        by_tag[t.get("enter_tag") or "(none)"].append(t)
    print()
    print("=== by enter tag ===")
    for k, ts in sorted(by_tag.items(), key=lambda kv: -len(kv[1])):
        ps = [t["profit_ratio"] for t in ts]
        wins = sum(1 for p in ps if p > 0)
        pnl = sum(t["profit_abs"] for t in ts)
        print(f"  {k:<28} n={len(ts):<3} pnl=${pnl:>8.2f} avg={mean(ps)*100:>6.2f}% wr={wins/len(ts)*100:>5.1f}%")

    # Long/short
    print()
    longs = [t for t in trades if not t.get("is_short", False)]
    shorts = [t for t in trades if t.get("is_short", False)]
    for label, group in (("long", longs), ("short", shorts)):
        if not group:
            continue
        ps = [t["profit_ratio"] for t in group]
        wins = sum(1 for p in ps if p > 0)
        pnl = sum(t["profit_abs"] for t in group)
        print(f"  {label}: n={len(group)} pnl=${pnl:>7.2f} wr={wins/len(group)*100:.1f}% avg={mean(ps)*100:.2f}%")

    # Per-pair
    print()
    print("=== by pair ===")
    by_pair = defaultdict(list)
    for t in trades:
        by_pair[t["pair"]].append(t)
    for k, ts in sorted(by_pair.items(), key=lambda kv: -sum(t["profit_abs"] for t in kv[1])):
        ps = [t["profit_ratio"] for t in ts]
        wins = sum(1 for p in ps if p > 0)
        pnl = sum(t["profit_abs"] for t in ts)
        best = max(ps)
        worst = min(ps)
        print(f"  {k:<22} n={len(ts):<3} pnl=${pnl:>8.2f} avg={mean(ps)*100:>6.2f}% wr={wins/len(ts)*100:>5.1f}%  best={best*100:>6.2f}% worst={worst*100:>6.2f}%")

    # Hold time
    durations = [t.get("trade_duration", 0) for t in trades]  # in candles? minutes?
    print()
    print(f"=== hold (mins) median={median(durations):.0f}  p25={sorted(durations)[len(durations)//4]}  p75={sorted(durations)[3*len(durations)//4]}")
    # leverage actually used
    levs = [t.get("leverage", 1) for t in trades if t.get("leverage")]
    if levs:
        print(f"=== leverage avg={mean(levs):.2f}  median={median(levs):.2f}  range={min(levs)}~{max(levs)}")

    # MFE/MAE if available
    if trades and "max_rate" in trades[0]:
        mfes, maes = [], []
        for t in trades:
            entry = t["open_rate"]
            mx = t.get("max_rate", entry)
            mn = t.get("min_rate", entry)
            if t.get("is_short"):
                mfe = (entry - mn) / entry * 100
                mae = (mx - entry) / entry * 100
            else:
                mfe = (mx - entry) / entry * 100
                mae = (entry - mn) / entry * 100
            mfes.append(mfe)
            maes.append(mae)
        print(f"=== MFE avg={mean(mfes):.2f}%  MAE avg={mean(maes):.2f}%")


if __name__ == "__main__":
    p = sys.argv[1] if len(sys.argv) > 1 else "data/audit/backtest_supertrend/backtest-result-2026-04-23_05-10-30.json"
    main(p)
