#!/usr/bin/env python3
"""分析交易日誌 — 從 trade_journal.jsonl 計算策略績效.

Usage:
    python scripts/analyze_journal.py
    python scripts/analyze_journal.py --pair ETH
    python scripts/analyze_journal.py --grade A
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JOURNAL_PATH = Path("/data/trade_journal.jsonl")
LOCAL_JOURNAL = PROJECT_ROOT / "data" / "trade_journal.jsonl"


def load_journal() -> list[dict]:
    path = JOURNAL_PATH if JOURNAL_PATH.exists() else LOCAL_JOURNAL
    if not path.exists():
        print(f"Journal not found at {path}")
        return []
    entries = []
    with open(path) as f:
        for line in f:
            try:
                entries.append(json.loads(line.strip()))
            except Exception:
                continue
    return entries


def analyze(entries: list[dict], pair_filter: str = "", grade_filter: str = ""):
    # Separate entries and exits
    entry_records = [e for e in entries if e.get("event") == "ENTRY"]
    exit_records = [e for e in entries if e.get("event") == "EXIT"]

    # Apply filters
    if pair_filter:
        exit_records = [e for e in exit_records if pair_filter.upper() in e.get("pair", "").upper()]

    if not exit_records:
        print("No closed trades to analyze.")
        return

    # Build entry lookup
    entry_by_pair: dict[str, dict] = {}
    for e in entry_records:
        entry_by_pair[e.get("pair", "")] = e

    # Per-grade stats
    grade_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "r_sum": 0.0, "dur_sum": 0.0})
    pair_stats: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    conf_buckets: dict[str, dict] = defaultdict(lambda: {"wins": 0, "losses": 0})

    total_pnl = 0.0
    wins = 0
    losses = 0

    for ex in exit_records:
        pnl = ex.get("pnl_usd", 0)
        r_mult = ex.get("r_multiple", 0)
        dur = ex.get("duration_min", 0)
        pair = ex.get("pair", "?")

        en = entry_by_pair.get(pair, {})
        grade = en.get("grade", "?")
        conf = en.get("confidence", 0)

        if grade_filter and grade != grade_filter:
            continue

        total_pnl += pnl
        is_win = pnl > 0
        if is_win:
            wins += 1
        else:
            losses += 1

        grade_stats[grade]["wins" if is_win else "losses"] += 1
        grade_stats[grade]["pnl"] += pnl
        grade_stats[grade]["r_sum"] += r_mult
        grade_stats[grade]["dur_sum"] += dur

        p_short = pair.replace("/USDT:USDT", "")
        pair_stats[p_short]["wins" if is_win else "losses"] += 1
        pair_stats[p_short]["pnl"] += pnl

        # Confidence bucket
        if conf < 0.4:
            bucket = "<0.4"
        elif conf < 0.6:
            bucket = "0.4-0.6"
        elif conf < 0.8:
            bucket = "0.6-0.8"
        else:
            bucket = "0.8+"
        conf_buckets[bucket]["wins" if is_win else "losses"] += 1

    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0

    print(f"\n{'='*50}")
    print(f"交易日誌分析")
    print(f"{'='*50}")
    print(f"交易: {total}筆 | 勝率: {wr:.1f}% | 淨利: ${total_pnl:+.2f}")
    print()

    # Grade breakdown
    print("--- Grade 分析 ---")
    for g in ["A", "B+", "B", "?"]:
        s = grade_stats.get(g)
        if not s:
            continue
        t = s["wins"] + s["losses"]
        if t == 0:
            continue
        gwr = s["wins"] / t * 100
        avg_r = s["r_sum"] / t
        avg_dur = s["dur_sum"] / t
        print(f"  Grade {g}: {t}筆 {gwr:.0f}%W avg_R={avg_r:+.1f} avg_dur={avg_dur:.0f}m P/L=${s['pnl']:+.2f}")

    # Pair breakdown
    print("\n--- 幣對分析 ---")
    for p, s in sorted(pair_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        t = s["wins"] + s["losses"]
        pwr = s["wins"] / t * 100 if t > 0 else 0
        print(f"  {p}: {t}筆 {pwr:.0f}%W P/L=${s['pnl']:+.2f}")

    # Confidence breakdown
    print("\n--- Confidence 分析 ---")
    for bucket in ["<0.4", "0.4-0.6", "0.6-0.8", "0.8+"]:
        s = conf_buckets.get(bucket)
        if not s:
            continue
        t = s["wins"] + s["losses"]
        if t == 0:
            continue
        bwr = s["wins"] / t * 100
        print(f"  Conf {bucket}: {t}筆 {bwr:.0f}%W")


if __name__ == "__main__":
    pair_f = ""
    grade_f = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--pair="):
            pair_f = arg.split("=")[1]
        elif arg.startswith("--grade="):
            grade_f = arg.split("=")[1]

    entries = load_journal()
    if entries:
        analyze(entries, pair_filter=pair_f, grade_filter=grade_f)
    else:
        print("No journal entries found.")
