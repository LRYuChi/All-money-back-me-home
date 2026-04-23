"""End-to-end smoke test for smart_money pipeline.

Runs the entire P1 → P2 → P3 flow with synthetic data (no network, no DB).
Use this to verify the pipeline before running against real Hyperliquid data.

    .venv/bin/python scripts/smart_money_smoke.py
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Allow running directly without `pip install -e .`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from smart_money.backtest.reporter import format_gate_decision, format_report
from smart_money.backtest.validator import evaluate_multi_cutoff
from smart_money.config import settings
from smart_money.ranking.metrics import compute_all
from smart_money.ranking.scorer import score_wallet
from smart_money.store.db import InMemoryStore
from smart_money.store.schema import Trade

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def synth_wallet(store: InMemoryStore, addr: str, *,
                 start: datetime, days: int,
                 pnl_seq: list[float], size_seq: list[float] | None = None):
    w = store.upsert_wallet(addr, seen_at=start)
    interval = timedelta(days=days / len(pnl_seq))
    symbols = ("BTC", "ETH", "SOL")
    trades: list[Trade] = []
    for i, pnl in enumerate(pnl_seq):
        ts_o = start + i * interval
        ts_c = ts_o + timedelta(hours=5)
        size = size_seq[i] if size_seq else 1.0
        sym = symbols[i % 3]
        trades.append(Trade(w.id, f"{i}-o", sym, "long", "open", size, 50000, None, 0.05, ts_o))
        trades.append(Trade(w.id, f"{i}-c", sym, "long", "close", size, 50000, pnl, 0.05, ts_c))
    store.upsert_trades(trades)
    return w


def main() -> None:
    print("=" * 70)
    print("Smart Money pipeline smoke test (no network)")
    print("=" * 70)

    store = InMemoryStore()
    T0 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    cutoff = T0 + timedelta(days=365)

    # Populate
    print("\n[1] Populating InMemoryStore with 8 synthetic wallets…")
    # 3 golden whales
    for i in range(3):
        pre = [10.0 if j % 3 != 0 else -3.0 for j in range(120)]   # PF ~ 4
        post = [9.0 if j % 3 != 0 else -3.5 for j in range(80)]
        w = synth_wallet(store, f"0x{'a' * 39}{i}", start=T0, days=365, pnl_seq=pre)
        # append post
        interval = timedelta(days=180 / len(post))
        symbols = ("BTC", "ETH", "SOL")
        for j, pnl in enumerate(post):
            ts_o = cutoff + j * interval
            ts_c = ts_o + timedelta(hours=5)
            sym = symbols[j % 3]
            store.upsert_trades([
                Trade(w.id, f"p{j}-o", sym, "long", "open", 1.0, 50000, None, 0.05, ts_o),
                Trade(w.id, f"p{j}-c", sym, "long", "close", 1.0, 50000, pnl, 0.05, ts_c),
            ])

    # 2 lucky gamblers (high pre via martingale, blowup post)
    for i in range(2):
        pre = [-4.0] * 30 + [50.0] * 30 + [5.0] * 60
        sizes = [1.0] * 30 + [4.0] * 30 + [1.0] * 60
        post = [-30.0] * 80
        w = synth_wallet(store, f"0x{'b' * 39}{i}", start=T0, days=365,
                         pnl_seq=pre, size_seq=sizes)
        interval = timedelta(days=180 / len(post))
        symbols = ("BTC", "ETH", "SOL")
        for j, pnl in enumerate(post):
            ts_o = cutoff + j * interval
            ts_c = ts_o + timedelta(hours=3)
            store.upsert_trades([
                Trade(w.id, f"p{j}-o", symbols[j % 3], "long", "open", 1.0, 50000, None, 0.05, ts_o),
                Trade(w.id, f"p{j}-c", symbols[j % 3], "long", "close", 1.0, 50000, pnl, 0.05, ts_c),
            ])

    # 3 mediocre
    for i in range(3):
        pre = [2.0 if j % 2 else -2.0 for j in range(100)]
        post = [1.5 if j % 2 else -2.0 for j in range(50)]
        w = synth_wallet(store, f"0x{'c' * 39}{i}", start=T0, days=365, pnl_seq=pre)
        interval = timedelta(days=180 / len(post))
        symbols = ("BTC", "ETH", "SOL")
        for j, pnl in enumerate(post):
            ts_o = cutoff + j * interval
            ts_c = ts_o + timedelta(hours=6)
            store.upsert_trades([
                Trade(w.id, f"p{j}-o", symbols[j % 3], "long", "open", 1.0, 50000, None, 0.05, ts_o),
                Trade(w.id, f"p{j}-c", symbols[j % 3], "long", "close", 1.0, 50000, pnl, 0.05, ts_c),
            ])

    n_wallets = len(store.list_wallets())
    print(f"   stored {n_wallets} wallets")

    # P2 ranking
    print("\n[2] Scoring all wallets (top 5)…")
    scored = []
    for w in store.list_wallets():
        trades = store.get_trades(w.id, until=cutoff)
        metrics = compute_all(trades)
        sb = score_wallet(metrics, config=settings.ranking)
        scored.append((w.address, sb.score, metrics))
    scored.sort(key=lambda x: -x[1])

    print(f"   {'#':>3}  {'address':<44}  score    PF   Sortino  Martin")
    for i, (addr, score, m) in enumerate(scored[:5], 1):
        print(f"   {i:>3}  {addr:<44}  {score:.4f}  {m.profit_factor:4.2f}  "
              f"{m.sortino:6.2f}   {m.martingale_penalty:.2f}")

    # P3 backtest + gate
    print("\n[3] Running multi-cutoff backtest gate…")
    cutoffs = [cutoff - timedelta(days=60), cutoff, cutoff + timedelta(days=60)]
    reports, decision = evaluate_multi_cutoff(
        store, cutoffs, forward_months=3, top_n=3,
        ranking_config=settings.ranking,
    )
    for r in reports:
        print("\n" + format_report(r))
    print(format_gate_decision(decision))

    print("\n" + "=" * 70)
    print("✅ Smoke test complete — pipeline works end-to-end.")
    print("=" * 70)


if __name__ == "__main__":
    main()
