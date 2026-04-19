"""Hyperliquid 錢包掃描 CLI — Phase 1.

用法:
    # 用預設 seed 檔 (smart_money/data/seeds.yaml + data/smart_money/watchlist.yaml)
    python -m smart_money.cli.scan --backfill-days 90

    # 指定 seed 檔
    python -m smart_money.cli.scan --seed path/to/seeds.yaml --backfill-days 30

    # 單一錢包
    python -m smart_money.cli.scan --address 0xabc...
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from hyperliquid.info import Info

from smart_money.config import settings
from smart_money.scanner.hl_client import HLClient
from smart_money.scanner.historical import backfill_batch
from smart_money.scanner.seeds import load_default_seeds, load_seed_file
from smart_money.store.db import build_store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.scan",
        description="Scan Hyperliquid wallets and persist trade history.",
    )
    parser.add_argument(
        "--address",
        action="append",
        metavar="ADDR",
        help="Single address to scan (can be passed multiple times).",
    )
    parser.add_argument(
        "--seed",
        type=Path,
        metavar="YAML",
        help="Path to a seed yaml file (overrides defaults).",
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=90,
        help="Days of history to backfill (default: 90).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just print what would be scanned, don't actually fetch.",
    )
    return parser


def _collect_addresses(args: argparse.Namespace) -> list[str]:
    if args.address:
        return [a.lower() for a in args.address]
    if args.seed:
        return load_seed_file(args.seed)
    return load_default_seeds()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    addresses = _collect_addresses(args)
    if not addresses:
        logger.error(
            "No addresses to scan. Add some to %s or pass --address.",
            "smart_money/data/seeds.yaml",
        )
        return 1

    logger.info("Scanning %d wallet(s), lookback=%d days", len(addresses), args.backfill_days)

    if args.dry_run:
        for a in addresses:
            print(f"[dry-run] would scan {a}")
        return 0

    store = build_store(settings)
    info = Info(base_url=settings.hl_api_url, skip_ws=True)
    client = HLClient(info)

    def on_progress(idx: int, total: int, result) -> None:
        logger.info(
            "[%d/%d] %s: new=%d total=%d%s",
            idx, total, result.address, result.trades_inserted, result.trades_total,
            f" [{result.skipped_reason}]" if result.skipped_reason else "",
        )

    results = backfill_batch(
        store=store,
        client=client,
        addresses=addresses,
        lookback_days=args.backfill_days,
        on_progress=on_progress,
    )

    total_new = sum(r.trades_inserted for r in results)
    total_trades = sum(r.trades_total for r in results)
    errors = sum(1 for r in results if r.skipped_reason and r.skipped_reason.startswith("error"))

    logger.info(
        "=== Scan complete: wallets=%d new_trades=%d total_trades=%d errors=%d ===",
        len(results), total_new, total_trades, errors,
    )
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
