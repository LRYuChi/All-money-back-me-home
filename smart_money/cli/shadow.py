"""Shadow mode daemon — Phase 4 實作."""
from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.shadow",
        description="Run shadow mode (paper trading) daemon subscribing to HL ws fills.",
    )
    parser.add_argument(
        "--whitelist",
        type=str,
        help="Path to whitelist yaml (default: load latest ranking snapshot).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raise NotImplementedError(
        f"smart_money.cli.shadow is a Phase 4 stub (args={vars(args)}). "
        "Implementation pending — see docs/SMART_MONEY_MIGRATION.md §3 Phase 4."
    )


if __name__ == "__main__":
    sys.exit(main())
