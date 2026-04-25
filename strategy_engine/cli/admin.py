"""CLI: admin commands for the strategy registry.

Usage:
    python -m strategy_engine.cli.admin list [--all]
    python -m strategy_engine.cli.admin enable <strategy_id> --reason <text> [--actor <text>]
    python -m strategy_engine.cli.admin disable <strategy_id> --reason <text> [--actor <text>]
    python -m strategy_engine.cli.admin history <strategy_id> [--limit N]

The `enable` / `disable` commands write to `strategy_enable_history` so
later runs of `history` (or postgres SELECT) show the full audit trail of
who/what flipped which strategy and why. G9 ConsecutiveLossDays should
call `set_enabled(..., actor="guard:consecutive_loss_cb")` directly from
the daemon — humans use this CLI to review and unlock.

Exit codes:
    0  — OK
    1  — strategy not found / IO failure
    2  — invalid args
"""
from __future__ import annotations

import argparse
import logging
import sys

from smart_money.config import settings
from strategy_engine.registry import StrategyNotFound, build_registry

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m strategy_engine.cli.admin",
        description="Strategy registry admin: list / enable / disable / history.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="List strategies")
    p_list.add_argument(
        "--all", action="store_true",
        help="Include disabled strategies (default: active only).",
    )

    for verb in ("enable", "disable"):
        sp = sub.add_parser(verb, help=f"{verb.capitalize()} a strategy.")
        sp.add_argument("strategy_id")
        sp.add_argument(
            "--reason", required=True,
            help="Required free-form reason; kept in audit history.",
        )
        sp.add_argument(
            "--actor", default="cli:strategy admin",
            help="Who triggered this flip (default: cli:strategy admin).",
        )

    p_hist = sub.add_parser("history", help="Show enable/disable audit log.")
    p_hist.add_argument("strategy_id")
    p_hist.add_argument(
        "--limit", type=int, default=20,
        help="Max events to print (default 20).",
    )

    return p


def _cmd_list(reg, all_: bool) -> int:
    records = reg.list_all() if all_ else reg.list_active()
    if not records:
        print("(no strategies)")
        return 0
    for rec in records:
        flag = "ON " if rec.parsed.enabled else "off"
        print(
            f"[{flag}] {rec.id:<40s} mode={rec.parsed.mode:<7s} "
            f"market={rec.parsed.market:<6s} symbol={rec.parsed.symbol} "
            f"tf={rec.parsed.timeframe}",
        )
    return 0


def _cmd_set_enabled(reg, sid: str, enabled: bool, reason: str, actor: str) -> int:
    try:
        rec = reg.set_enabled(sid, enabled, reason=reason, actor=actor)
    except StrategyNotFound:
        print(f"error: strategy {sid!r} not found", file=sys.stderr)
        return 1
    state = "ENABLED" if rec.parsed.enabled else "DISABLED"
    print(f"{sid}: {state} (reason={reason!r}, actor={actor!r})")
    return 0


def _cmd_history(reg, sid: str, limit: int) -> int:
    try:
        events = reg.enable_history(sid, limit=limit)
    except StrategyNotFound:
        # Some backends raise on lookup; InMemory returns empty list either way
        print(f"error: strategy {sid!r} not found", file=sys.stderr)
        return 1
    if not events:
        print(f"(no audit history for {sid!r})")
        return 0
    print(f"{sid}: {len(events)} event(s) (newest first)")
    for ev in events:
        flag = "ENABLE " if ev.enabled else "DISABLE"
        ts = ev.created_at.isoformat() if ev.created_at else "?"
        actor = ev.actor or "-"
        reason = ev.reason or ""
        print(f"  {ts}  {flag}  {actor:<32s}  {reason}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    reg = build_registry(settings)

    if args.cmd == "list":
        return _cmd_list(reg, args.all)
    if args.cmd == "enable":
        return _cmd_set_enabled(reg, args.strategy_id, True, args.reason, args.actor)
    if args.cmd == "disable":
        return _cmd_set_enabled(reg, args.strategy_id, False, args.reason, args.actor)
    if args.cmd == "history":
        return _cmd_history(reg, args.strategy_id, args.limit)
    return 2


if __name__ == "__main__":
    sys.exit(main())
