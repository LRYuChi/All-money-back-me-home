"""歷史回測 CLI(防線 A Go/No-Go gate)— Phase 3 實作."""
from __future__ import annotations

import argparse
import sys
from datetime import date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.backtest",
        description="Walk-forward backtest: rank wallets at t0, evaluate PnL over next N months.",
    )
    parser.add_argument(
        "--cutoff",
        type=date.fromisoformat,
        required=True,
        help="t0 cutoff date (YYYY-MM-DD). Only data before this point is used for ranking.",
    )
    parser.add_argument(
        "--forward-months",
        type=int,
        default=12,
        help="Evaluation window in months after cutoff (default: 12).",
    )
    parser.add_argument("--top", type=int, default=20, help="Number of wallets to evaluate.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(
        f"smart_money.cli.backtest is a Phase 3 stub (args={vars(args)}). "
        "Implementation pending — see docs/SMART_MONEY_MIGRATION.md §3 Phase 3."
    )


if __name__ == "__main__":
    sys.exit(main())
