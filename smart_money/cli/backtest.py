"""歷史回測 CLI(防線 A Go/No-Go gate)— Phase 3.

用法:
    # 單一切點
    python -m smart_money.cli.backtest --cutoff 2025-10-31 --forward-months 6

    # Multi-cutoff rolling(推薦,避免單一時點 overfit)
    python -m smart_money.cli.backtest \\
        --cutoff 2025-04-30 --cutoff 2025-07-31 --cutoff 2025-10-31 \\
        --forward-months 6 --top 20
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timezone

from smart_money.backtest.reporter import (
    format_gate_decision,
    format_report,
    report_to_json,
)
from smart_money.backtest.validator import decide_gate, evaluate_multi_cutoff, run_backtest
from smart_money.config import settings
from smart_money.store.db import build_store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.backtest",
        description="Walk-forward backtest (Go/No-Go gate, MIGRATION.md §3 Phase 3).",
    )
    parser.add_argument(
        "--cutoff",
        type=date.fromisoformat,
        action="append",
        required=True,
        help="t0 cutoff date(s); pass multiple --cutoff for multi-cutoff rolling.",
    )
    parser.add_argument(
        "--forward-months",
        type=int,
        default=12,
        help="Evaluation window in months (default: 12).",
    )
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON payload to stdout (in addition to human-readable).",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    store = build_store(settings)
    cutoff_dts = [datetime.combine(c, datetime.min.time(), tzinfo=timezone.utc)
                  for c in args.cutoff]

    if len(cutoff_dts) == 1:
        report = run_backtest(
            store, cutoff_dts[0],
            forward_months=args.forward_months,
            top_n=args.top,
            ranking_config=settings.ranking,
        )
        decision = decide_gate(report)

        print(format_report(report))
        print(format_gate_decision(decision))
        if args.json:
            print("\n=== JSON ===")
            print(report_to_json(report))
        return 0 if decision.passed else 1

    # Multi-cutoff
    reports, decision = evaluate_multi_cutoff(
        store, cutoff_dts,
        forward_months=args.forward_months,
        top_n=args.top,
        ranking_config=settings.ranking,
    )
    for r in reports:
        print(format_report(r))
    print(format_gate_decision(decision))
    if args.json:
        print("\n=== JSON per cutoff ===")
        for r in reports:
            print(report_to_json(r))

    return 0 if decision.passed else 1


if __name__ == "__main__":
    sys.exit(main())
