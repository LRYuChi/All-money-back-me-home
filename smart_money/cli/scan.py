"""Hyperliquid 錢包掃描 CLI — Phase 1 實作."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.scan",
        description="Scan Hyperliquid wallets and persist trade history to Supabase.",
    )
    parser.add_argument(
        "--seed-leaderboard",
        action="store_true",
        help="Seed with Top 500 from HL leaderboard (Phase 1).",
    )
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=90,
        help="Days of history to backfill per wallet (default: 90).",
    )
    parser.add_argument("--resume", action="store_true", help="Resume from last checkpoint.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(
        f"smart_money.cli.scan is a Phase 1 stub (args={vars(args)}). "
        "Implementation pending — see docs/SMART_MONEY_MIGRATION.md §3 Phase 1."
    )


if __name__ == "__main__":
    sys.exit(main())
