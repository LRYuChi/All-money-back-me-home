"""Live trade daemon(需 SM_MODE=live 才真實下單)— Phase 5 實作."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.trade",
        description="Live trading daemon: HL signals → guards → OKX execution.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run even if SM_MODE=live.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(
        f"smart_money.cli.trade is a Phase 5 stub (args={vars(args)}). "
        "Implementation pending — see docs/SMART_MONEY_MIGRATION.md §3 Phase 5."
    )


if __name__ == "__main__":
    sys.exit(main())
