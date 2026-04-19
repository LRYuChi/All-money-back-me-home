"""Ranking CLI — Phase 2.

讀 store 中的錢包與交易,套用 filters → metrics → scorer → 輸出 Top N
並持久化到 sm_rankings table.

用法:
    python -m smart_money.cli.rank --top 50
    python -m smart_money.cli.rank --snapshot-date 2026-04-19 --top 20
    python -m smart_money.cli.rank --explain --top 5
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone

from smart_money.config import settings
from smart_money.ranking.filters import FilterThresholds, apply_filters
from smart_money.ranking.metrics import compute_all
from smart_money.ranking.scorer import score_and_rank
from smart_money.store.db import build_store
from smart_money.store.schema import Ranking

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.rank",
        description="Compute wallet rankings from stored trades.",
    )
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        help="Snapshot cutoff date (YYYY-MM-DD). Default: today UTC.",
    )
    parser.add_argument("--top", type=int, default=50, help="Output top N wallets.")
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print per-wallet score breakdown (slow for large N).",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Don't write rankings to store (preview mode).",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    snapshot = args.snapshot_date or date.today()
    snapshot_dt = datetime.combine(snapshot, datetime.min.time(), tzinfo=timezone.utc)

    store = build_store(settings)
    wallets = store.list_wallets()
    if not wallets:
        logger.error("Store contains no wallets. Run `smart_money.cli.scan` first.")
        return 1

    logger.info("Scoring %d wallet(s)…", len(wallets))

    # Step 1: filter + compute metrics
    thresholds = FilterThresholds(
        min_sample_size=settings.ranking.min_sample_size,
        min_active_days=settings.ranking.min_active_days,
        max_symbol_concentration=settings.ranking.max_symbol_concentration,
        min_avg_holding_seconds=settings.ranking.min_avg_holding_seconds,
    )

    # Perf: HFT bots 有 20k-50k trades 單次 fetch 會卡爆 pgbouncer.
    # 用 count_trades (cheap) 先把「不可能通過 HFT filter」的 wallet 過濾掉.
    # 假設 avg holding ≥ 600s,單日最多 144 closes → 365 天最多 52,560.
    # 取 30,000 作 upper bound(給一些彈性,但擋掉 > 50k 的極端 bot).
    MAX_TRADES_FOR_CONSIDERATION = 30_000

    eligible: list = []
    filtered_out = 0
    skipped_hft = 0
    for w in wallets:
        n_trades = store.count_trades(w.id)
        if n_trades > MAX_TRADES_FOR_CONSIDERATION:
            logger.debug("pre-filter: %s skipped (%d trades = HFT bot)", w.address, n_trades)
            skipped_hft += 1
            continue
        if n_trades < settings.ranking.min_sample_size:
            logger.debug("pre-filter: %s skipped (%d < sample_size floor)", w.address, n_trades)
            filtered_out += 1
            continue

        trades = store.get_trades(w.id, until=snapshot_dt)
        verdict = apply_filters(trades, thresholds=thresholds)
        if not verdict.passed:
            logger.debug("filter: %s dropped (%s)", w.address, verdict.reason)
            filtered_out += 1
            continue
        metrics = compute_all(trades)
        eligible.append((w, metrics))

    logger.info("  pre-filter: skipped_hft=%d filtered_early=%d remaining=%d",
                skipped_hft, filtered_out, len(eligible))

    logger.info("  eligible=%d filtered_out=%d", len(eligible), filtered_out)
    if not eligible:
        logger.warning("No wallet passed filters; nothing to rank.")
        return 2

    # Step 2: score + rank
    scored = score_and_rank(
        [(str(w.id), m) for w, m in eligible],
        config=settings.ranking,
    )

    # Step 3: output top N
    top_n = scored[: args.top]
    print(f"\n=== Rankings @ {snapshot.isoformat()} (top {len(top_n)} of {len(scored)}) ===")
    print(f"{'#':>3}  {'address':<44}  score    sample  PnL")
    wallet_by_id = {str(w.id): w for w, _ in eligible}
    metrics_by_id = {str(w.id): m for w, m in eligible}

    rankings: list[Ranking] = []
    for rank_idx, (wid, sb) in enumerate(top_n, start=1):
        w = wallet_by_id[wid]
        m = metrics_by_id[wid]
        print(f"{rank_idx:>3}  {w.address:<44}  {sb.score:.4f}   {m.sample_size:>4}   {m.total_pnl:+,.2f}")
        if args.explain:
            print(sb.explain())
            print()

        rankings.append(Ranking(
            snapshot_date=snapshot_dt,
            wallet_id=w.id,
            rank=rank_idx,
            score=sb.score,
            metrics={
                **m.to_dict(),
                "components": sb.components,
                "contributions": sb.contributions,
            },
        ))

    # Step 4: persist
    if not args.no_persist:
        n_saved = store.save_ranking(rankings)
        logger.info("Persisted %d ranking rows for %s", n_saved, snapshot.isoformat())
    else:
        logger.info("--no-persist: skipping store.save_ranking")

    # Emit JSON summary for downstream tooling
    summary = {
        "snapshot_date": snapshot.isoformat(),
        "wallets_scored": len(scored),
        "wallets_filtered_out": filtered_out,
        "top": [
            {"rank": i, "address": wallet_by_id[wid].address, "score": sb.score}
            for i, (wid, sb) in enumerate(top_n, start=1)
        ],
    }
    logger.info("summary: %s", json.dumps({k: v for k, v in summary.items() if k != "top"}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
