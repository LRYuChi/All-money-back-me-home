"""排名引擎 CLI — Phase 2 實作."""
from __future__ import annotations

import argparse
import sys
from datetime import date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.rank",
        description="Compute wallet rankings and persist snapshot.",
    )
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        help="Snapshot cutoff date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument("--top", type=int, default=50, help="Output top N wallets (default: 50).")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(
        f"smart_money.cli.rank is a Phase 2 stub (args={vars(args)}). "
        "Implementation pending — see docs/SMART_MONEY_MIGRATION.md §3 Phase 2."
    )


if __name__ == "__main__":
    sys.exit(main())
