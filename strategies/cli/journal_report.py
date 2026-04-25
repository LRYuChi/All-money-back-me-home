"""CLI: print Supertrend trade-journal performance report (round 46).

Usage:
    # All-time snapshot
    python -m strategies.cli.journal_report

    # Last 7 days
    python -m strategies.cli.journal_report --days 7

    # Last 30 trades
    python -m strategies.cli.journal_report --trades 30

    # JSON output (for piping into dashboards / Slack bots)
    python -m strategies.cli.journal_report --format json

    # Specific date range
    python -m strategies.cli.journal_report \\
        --from 2026-04-01 --to 2026-04-25

    # Custom journal directory
    python -m strategies.cli.journal_report --dir trading_log/journal_demo

Exit codes:
    0  — report printed (snapshot may be empty if no closed trades)
    2  — invalid args / unparseable date
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from strategies.journal import TradeJournal
from strategies.performance import (
    PerformanceAggregator,
    PerformanceSnapshot,
    format_snapshot_md,
)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategies.cli.journal_report",
        description="Print Supertrend trade journal performance snapshot.",
    )
    p.add_argument(
        "--dir", type=Path, default=Path("trading_log/journal"),
        help="Journal directory (default trading_log/journal).",
    )
    window = p.add_mutually_exclusive_group()
    window.add_argument(
        "--days", type=int,
        help="Aggregate the last N days (UTC).",
    )
    window.add_argument(
        "--trades", type=int,
        help="Aggregate the last N closed trades.",
    )
    p.add_argument(
        "--from", dest="from_date", type=_parse_date,
        help="Start date (UTC, YYYY-MM-DD).",
    )
    p.add_argument(
        "--to", dest="to_date", type=_parse_date,
        help="End date (UTC, YYYY-MM-DD).",
    )
    p.add_argument(
        "--format", choices=["md", "json", "compact"], default="md",
        help="Output format: md (default human Markdown), json (machine), "
             "compact (one-line summary).",
    )
    return p


def _parse_date(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"date must be YYYY-MM-DD, got {s!r}"
        )


def _format_compact(snap: PerformanceSnapshot) -> str:
    if snap.n_trades == 0:
        return "n=0 (no trades)"
    return (
        f"n={snap.n_trades} wr={snap.win_rate:.1%} "
        f"pf={snap.profit_factor:.2f} "
        f"exp={snap.expectancy_pct:+.2f}% "
        f"pnl=${snap.sum_pnl_usd:+.2f} "
        f"dd=-{snap.max_drawdown_pct:.2f}% "
        f"streak={snap.current_streak:+d}"
    )


def _format_json(snap: PerformanceSnapshot) -> str:
    payload = asdict(snap)
    # by_tag/by_pair/by_exit_reason values are GroupStats dataclasses;
    # asdict on the parent already recursively converts them
    return json.dumps(payload, indent=2, default=str, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if not args.dir.exists():
        print(
            f"warning: journal directory {args.dir} does not exist yet — "
            f"will report empty snapshot",
            file=sys.stderr,
        )

    journal = TradeJournal(args.dir)
    agg = PerformanceAggregator(journal)

    # Resolve window
    from_date = args.from_date
    to_date = args.to_date
    if args.days is not None:
        from_date = datetime.now(timezone.utc) - timedelta(days=args.days)
        to_date = datetime.now(timezone.utc)

    snap = agg.snapshot(
        from_date=from_date,
        to_date=to_date,
        last_n_trades=args.trades,
    )

    if args.format == "json":
        print(_format_json(snap))
    elif args.format == "compact":
        print(_format_compact(snap))
    else:
        print(format_snapshot_md(snap))

    return 0


if __name__ == "__main__":
    sys.exit(main())
