"""Cron entry point: run one batch of signal validation.

Usage:
    python -m reflection.cli.validate
        --max-age-hours 336      # 14d default — anything older is hopeless
        --limit 200              # cap per invocation
        --threshold 0.002        # |return| under this → INCONCLUSIVE
        --log-level INFO

Schedule (production):
    cron: 17 * * * *      # hourly at :17 (off-minute, see CronCreate guidance)

Exit codes:
    0  — completed (even with 0 examined)
    1  — IO setup failure (no DB credentials)
    2  — unhandled exception during batch
"""
from __future__ import annotations

import argparse
import logging
import sys

from reflection.hl_price import build_hl_fetcher
from reflection.price import InMemoryPriceFetcher, PriceFetcher
from reflection.supabase_io import build_reader_updater
from reflection.validator import (
    DEFAULT_CORRECTNESS_THRESHOLD,
    validate_signals,
)
from smart_money.config import settings

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m reflection.cli.validate",
        description="Validate one batch of unvalidated signal_history rows.",
    )
    p.add_argument(
        "--max-age-hours", type=int, default=24 * 14,
        help="Don't bother with signals older than this (default 14d).",
    )
    p.add_argument(
        "--limit", type=int, default=200,
        help="Max rows per invocation (default 200).",
    )
    p.add_argument(
        "--threshold", type=float, default=DEFAULT_CORRECTNESS_THRESHOLD,
        help=f"|return| under this → INCONCLUSIVE (default {DEFAULT_CORRECTNESS_THRESHOLD}).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Run validator with InMemory IO so nothing is written. "
             "Useful as a smoke check before enabling the cron.",
    )
    p.add_argument(
        "--price-source",
        choices=["hl", "inmemory"],
        default="hl",
        help="Price backend (default hl). Use 'inmemory' for offline smoke.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dry_run:
        logger.info("validate: dry-run mode (InMemory IO; no DB writes)")
        from reflection.validator import SignalHistoryReader, SignalHistoryUpdater

        class _NoopReader:
            def read_unvalidated(self, *, max_age_hours, limit):
                return []

        class _NoopUpdater:
            def update_verdict(self, *args, **kwargs):
                pass

        reader: SignalHistoryReader = _NoopReader()
        updater: SignalHistoryUpdater = _NoopUpdater()
    else:
        try:
            reader, updater = build_reader_updater(settings)
        except RuntimeError as e:
            logger.error("validate: IO setup failed: %s", e)
            return 1

    # Price source — defaults to HL (Phase C round 8). Use --price-source
    # inmemory for offline smoke.
    prices: PriceFetcher
    if args.price_source == "inmemory":
        prices = InMemoryPriceFetcher()
        if not args.dry_run:
            logger.warning(
                "validate: --price-source inmemory → all rows will be "
                "MISSING_PRICE (no candles preloaded).",
            )
    else:
        try:
            prices = build_hl_fetcher()
        except Exception as e:
            logger.error("validate: HL fetcher init failed: %s", e)
            return 1

    try:
        stats = validate_signals(
            reader, updater, prices,
            max_age_hours=args.max_age_hours,
            limit=args.limit,
            correctness_threshold=args.threshold,
        )
    except Exception as e:
        logger.exception("validate: unhandled exception: %s", e)
        return 2

    logger.info(
        "validate: done. examined=%d correct=%d incorrect=%d "
        "inconclusive=%d missing=%d errors=%d hit_rate=%.2f coverage=%.2f",
        stats.examined, stats.correct, stats.incorrect,
        stats.inconclusive, stats.missing_price, stats.write_errors,
        stats.hit_rate, stats.coverage,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
