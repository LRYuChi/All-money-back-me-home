"""CLI: run a PendingOrderWorker for a single mode.

Usage:
    python -m execution.pending_orders.cli.work --mode shadow
    python -m execution.pending_orders.cli.work --mode shadow --process-once

For shadow / notify modes the LogOnlyDispatcher is used (no exchange
contact). Phase F.1 adds OKX live dispatcher; this CLI will accept
--dispatcher live to switch.

Exit codes:
    0  — clean shutdown (SIGINT/SIGTERM or --process-once with 0 work)
    1  — IO setup failure (queue not configured)
    2  — invalid args
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from execution.pending_orders import (
    LogOnlyDispatcher,
    NoOpPendingOrderQueue,
    PendingOrderWorker,
    build_queue,
)
from risk import (
    DailyLossCircuitBreakerGuard,
    GlobalExposureGuard,
    GuardPipeline,
    LatencyBudgetGuard,
    MinSizeGuard,
    PerMarketExposureGuard,
    PerStrategyExposureGuard,
    build_exposure_provider,
    build_pnl_aggregator,
    make_context_provider,
)
from smart_money.config import settings

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m execution.pending_orders.cli.work",
        description="Run a pending_orders worker for one mode.",
    )
    p.add_argument(
        "--mode", required=True,
        choices=["shadow", "paper", "live", "notify"],
        help="Execution mode this worker services.",
    )
    p.add_argument(
        "--idle-sleep", type=float, default=1.0,
        help="Seconds to sleep when queue is empty (default 1.0).",
    )
    p.add_argument(
        "--process-once", action="store_true",
        help="Process one order then exit (smoke + cron-style runs).",
    )
    p.add_argument(
        "--max-orders", type=int, default=0,
        help="Stop after processing N orders (0 = unlimited; default).",
    )
    p.add_argument(
        "--with-guards", action="store_true",
        help="Enable risk guard pipeline (G1 latency / G3 min_size / G4-G6 "
             "exposure caps / G8 daily loss CB) before dispatch.",
    )
    p.add_argument(
        "--capital-usd", type=float, default=10_000.0,
        help="Notional capital for exposure-cap calculations (default 10000).",
    )
    p.add_argument(
        "--max-strategy-pct", type=float, default=0.20,
        help="G4 single-strategy notional cap (default 0.20 = 20%% of capital).",
    )
    p.add_argument(
        "--max-market-pct", type=float, default=0.50,
        help="G5 default per-market cap (default 0.50 = 50%% of capital).",
    )
    p.add_argument(
        "--max-leverage", type=float, default=1.5,
        help="G6 global leverage limit (default 1.5x of capital).",
    )
    p.add_argument(
        "--daily-loss-pct", type=float, default=0.05,
        help="G8 daily loss CB threshold (default 0.05 = 5%% of capital).",
    )
    p.add_argument(
        "--min-notional-usd", type=float, default=10.0,
        help="G3 min order size USD (default 10).",
    )
    p.add_argument(
        "--latency-budget-sec", type=float, default=15.0,
        help="G1 latency budget in seconds (default 15).",
    )
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def _build_guard_pipeline(args: argparse.Namespace):
    """Construct the standard 6-guard pipeline + context_provider closure.

    Order matters: G1 (latency, fastest reject), G3 (size, deterministic),
    G8 (CB — early since once tripped, nothing else matters), G4/G5/G6
    (exposure caps, may scale).
    """
    pnl_agg = build_pnl_aggregator(settings)
    exposure = build_exposure_provider(settings)

    pipeline = GuardPipeline([
        LatencyBudgetGuard(budget_seconds=args.latency_budget_sec),
        MinSizeGuard(default_min_usd=args.min_notional_usd),
        DailyLossCircuitBreakerGuard(
            loss_threshold_pct=args.daily_loss_pct,
            pnl_aggregator=pnl_agg,
        ),
        PerStrategyExposureGuard(cap_pct_of_capital=args.max_strategy_pct),
        PerMarketExposureGuard(default_cap_pct=args.max_market_pct),
        GlobalExposureGuard(capital_multiplier=args.max_leverage),
    ])

    # Round 21: signal_age_provider not yet wired — depends on
    # signal_history.id linkage on the order. G1 fails open until that
    # lands; other 5 guards function independently.
    context_provider = make_context_provider(
        capital_usd=args.capital_usd,
        exposure=exposure,
        signal_age_provider=None,
    )

    logger.info(
        "guard pipeline: enabled (capital=$%.0f, max_strategy=%.0f%%, "
        "max_market=%.0f%%, max_leverage=%.1fx, daily_loss=%.0f%%, "
        "min_notional=$%.0f, latency_budget=%.0fs)",
        args.capital_usd, args.max_strategy_pct * 100,
        args.max_market_pct * 100, args.max_leverage,
        args.daily_loss_pct * 100, args.min_notional_usd,
        args.latency_budget_sec,
    )
    return pipeline, context_provider


async def run_worker(args: argparse.Namespace) -> int:
    queue = build_queue(settings)
    if isinstance(queue, NoOpPendingOrderQueue):
        logger.error(
            "worker: queue is NoOp (no DATABASE_URL or SUPABASE_URL+KEY in env). "
            "There's nothing to process. Exiting.",
        )
        return 1

    # Phase F round 17 ships LogOnlyDispatcher only. Phase F.1 will add
    # OKXLiveDispatcher and choose based on --mode.
    dispatcher = LogOnlyDispatcher(mode=args.mode)

    pipeline = None
    context_provider = None
    if args.with_guards:
        pipeline, context_provider = _build_guard_pipeline(args)

    worker = PendingOrderWorker(
        queue, dispatcher,
        idle_sleep_sec=args.idle_sleep,
        risk_pipeline=pipeline,
        context_provider=context_provider,
    )

    if args.process_once:
        n = worker.process_one()
        logger.info("process-once: %d order processed (stats=%s)", n, worker.stats())
        return 0

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, _on_signal)

    # Optional max-orders limit
    if args.max_orders > 0:
        async def _stopper() -> None:
            while not stop.is_set():
                await asyncio.sleep(1.0)
                if worker.stats()["claimed"] >= args.max_orders:
                    logger.info(
                        "max-orders %d reached — stopping", args.max_orders,
                    )
                    stop.set()
        asyncio.create_task(_stopper())

    await worker.run_forever(stop)
    logger.info("worker stopped: stats=%s", worker.stats())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run_worker(args))


if __name__ == "__main__":
    sys.exit(main())
