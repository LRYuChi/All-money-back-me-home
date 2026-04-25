"""CLI: run a PendingOrderWorker for a single mode.

Usage:
    python -m execution.pending_orders.cli.work --mode shadow
    python -m execution.pending_orders.cli.work --mode shadow --process-once

Dispatcher selection (round 24): the worker looks up the dispatcher for
`--mode` via `build_default_registry(settings)`:
    shadow / paper → LogOnlyDispatcher (no exchange contact)
    notify         → NotifyOnlyDispatcher (push via shared.notifier)
    live           → not registered yet → exits 1 (Phase F.1 adds it)

Exit codes:
    0  — clean shutdown (SIGINT/SIGTERM or --process-once with 0 work)
    1  — IO setup failure (queue not configured) or unsupported mode
    2  — invalid args
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from execution.pending_orders import (
    NoOpPendingOrderQueue,
    PendingOrderWorker,
    UnsupportedModeError,
    background_sweep_loop,
    build_default_registry,
    build_queue,
)
from execution.exchanges import NoOpSymbolCatalog, build_symbol_catalog
from risk import (
    ConsecutiveLossDaysGuard,
    CorrelationCapGuard,
    DailyLossCircuitBreakerGuard,
    GlobalExposureGuard,
    GuardPipeline,
    KellyPositionGuard,
    LatencyBudgetGuard,
    MinSizeGuard,
    NoOpCorrelationMatrix,
    NoOpWinRateProvider,
    PerMarketExposureGuard,
    PerStrategyExposureGuard,
    SymbolSupportedGuard,
    build_correlation_matrix,
    build_exposure_provider,
    build_pnl_aggregator,
    build_signal_age_provider,
    build_win_rate_provider,
    chain_handlers,
    make_context_provider,
    make_g9_strategy_disabler,
    make_guard_notifier_handler,
)
from smart_money.config import settings
from strategy_engine.registry import build_registry

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
        help="Enable risk guard pipeline (G1 latency / G2 symbol-supported "
             "(opt-in) / G3 min_size / G4-G6 exposure caps / G7 correlation "
             "cap (opt-in) / G8 daily loss CB / G9 consecutive losses / "
             "G10 Kelly (opt-in)) before dispatch.",
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
    p.add_argument(
        "--consecutive-loss-days", type=int, default=3,
        help="G9 consecutive losing days threshold (default 3 — matches D7).",
    )
    p.add_argument(
        "--symbol-supported", action="store_true",
        help="G2 deny orders for symbols not in the catalog (loaded from "
             "SM_SYMBOL_CATALOG_PATH; NoOp catalog → fail-open).",
    )
    p.add_argument(
        "--correlation-cap-pct", type=float, default=0.0,
        help="G7 correlation cluster cap as fraction of capital (0 = disabled, "
             "default 0; example: 0.40 = 40%% of capital across symbols whose "
             "|ρ| ≥ correlation-threshold).",
    )
    p.add_argument(
        "--correlation-threshold", type=float, default=0.70,
        help="G7 |ρ| threshold counting two symbols as part of the same "
             "cluster (default 0.70).",
    )
    p.add_argument(
        "--kelly-safety-factor", type=float, default=0.0,
        help="G10 Kelly safety factor (0 = guard disabled, default 0; "
             "0.25 = quarter-Kelly, recommended starting point).",
    )
    p.add_argument(
        "--kelly-min-trades", type=int, default=30,
        help="G10 minimum sample size before Kelly is applied (default 30).",
    )
    p.add_argument(
        "--kelly-lookback-days", type=int, default=30,
        help="G10 win-rate lookback window in days (default 30).",
    )
    p.add_argument(
        "--auto-disable-on-g9", action="store_true",
        help="When G9 trips, disable the order's strategy in the registry "
             "(audit row written via set_enabled). Manual unlock required.",
    )
    p.add_argument(
        "--alert-on-cb", action="store_true",
        help="Push a CRITICAL alert via shared.notifier on G8/G9 trip "
             "(implied by --auto-disable-on-g9; pass alone to alert without "
             "auto-disabling).",
    )
    p.add_argument(
        "--sweep-interval-sec", type=float, default=0,
        help="Run pending_orders sweeper as a background task every N "
             "seconds (0 = disabled). Implies opt-in via --sweep-pending-max-age "
             "and/or --sweep-dispatching-max-age.",
    )
    p.add_argument(
        "--sweep-pending-max-age", type=float, default=0,
        help="Background sweeper: PENDING older than N seconds → EXPIRED "
             "(0 = bucket disabled).",
    )
    p.add_argument(
        "--sweep-dispatching-max-age", type=float, default=0,
        help="Background sweeper: DISPATCHING older than N seconds → "
             "EXPIRED, worker likely crashed (0 = bucket disabled).",
    )
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def _build_guard_pipeline(args: argparse.Namespace):
    """Construct the standard 7-guard pipeline + context_provider closure.

    Order matters: G1 (latency, fastest reject), G3 (size, deterministic),
    G8 (CB — early since once tripped, nothing else matters), G4/G5/G6
    (exposure caps, may scale).
    """
    pnl_agg = build_pnl_aggregator(settings)
    exposure = build_exposure_provider(settings)
    age_provider = build_signal_age_provider(settings)

    guards: list = [
        LatencyBudgetGuard(budget_seconds=args.latency_budget_sec),
        MinSizeGuard(default_min_usd=args.min_notional_usd),
    ]

    # G2 placement: right after the cheap deterministic guards (G1, G3),
    # before any DB-touching guard. Catches typos/delistings without
    # spending DB queries on a doomed order.
    if args.symbol_supported:
        catalog = build_symbol_catalog(settings)
        if isinstance(catalog, NoOpSymbolCatalog):
            logger.warning(
                "G2 symbol-supported requested but catalog is NoOp "
                "(SM_SYMBOL_CATALOG_PATH unset / file missing) — "
                "guard will fail-open until a real catalog is configured",
            )
        guards.append(SymbolSupportedGuard(catalog=catalog))

    guards.extend([
        DailyLossCircuitBreakerGuard(
            loss_threshold_pct=args.daily_loss_pct,
            pnl_aggregator=pnl_agg,
        ),
        ConsecutiveLossDaysGuard(
            max_consecutive_losses=args.consecutive_loss_days,
            pnl_aggregator=pnl_agg,
        ),
        PerStrategyExposureGuard(cap_pct_of_capital=args.max_strategy_pct),
        PerMarketExposureGuard(default_cap_pct=args.max_market_pct),
    ])
    # G7 placement: between per-market and global. Reasoning — per-market
    # caps a whole asset class; G7 catches "too many strongly-correlated
    # symbols within that class"; G6 then enforces total leverage.
    if args.correlation_cap_pct > 0:
        matrix = build_correlation_matrix(settings)
        if isinstance(matrix, NoOpCorrelationMatrix):
            logger.warning(
                "G7 correlation cap requested but matrix is NoOp "
                "(SM_CORRELATION_MATRIX_PATH unset / file missing) — "
                "guard will fail-open until a real matrix is configured",
            )
        guards.append(CorrelationCapGuard(
            matrix=matrix,
            correlation_threshold=args.correlation_threshold,
            cluster_cap_pct=args.correlation_cap_pct,
        ))
    # G10 placement: just before global exposure. Kelly cap is a
    # per-strategy sanity check; global cap then enforces total leverage
    # regardless of any individual Kelly recommendation.
    if args.kelly_safety_factor > 0:
        wr_provider = build_win_rate_provider(settings)
        if isinstance(wr_provider, NoOpWinRateProvider):
            logger.warning(
                "G10 Kelly cap requested but win_rate_provider is NoOp "
                "(no DATABASE_URL) — guard will fail-open until trade "
                "history is queryable",
            )
        guards.append(KellyPositionGuard(
            win_rate_provider=wr_provider,
            safety_factor=args.kelly_safety_factor,
            min_trades=args.kelly_min_trades,
            lookback_days=args.kelly_lookback_days,
        ))
    guards.append(GlobalExposureGuard(capital_multiplier=args.max_leverage))
    pipeline = GuardPipeline(guards)

    # Round 23: G1 now real — signal_age looked up from fused_signals.ts
    # via order.fused_signal_id. Falls back to None (fail-open) when the
    # order has no fused_signal_id or the lookup fails.
    context_provider = make_context_provider(
        capital_usd=args.capital_usd,
        exposure=exposure,
        signal_age_provider=age_provider.age_seconds,
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


def _build_side_effects(args: argparse.Namespace):
    """Compose side-effect handlers based on CLI flags. Returns a single
    callable (or None) suitable for PendingOrderWorker.

    --auto-disable-on-g9 implies --alert-on-cb (you'd want to know when
    auto-disable fires). Pass --alert-on-cb alone to alert without
    flipping the registry — useful in dry-run modes.
    """
    handlers: list = []

    if args.auto_disable_on_g9:
        registry = build_registry(settings)
        handlers.append(make_g9_strategy_disabler(registry))
        logger.info(
            "G9 auto-disable enabled — strategies will be set_enabled=False "
            "in registry on consecutive_loss_cb DENY",
        )

    if args.alert_on_cb or args.auto_disable_on_g9:
        try:
            from shared.notifier import build_notifier
            notifier = build_notifier(settings)
            handlers.append(make_guard_notifier_handler(notifier))
            logger.info(
                "guard notifier alerts enabled for G8/G9 trips "
                "(notifier=%s)", type(notifier).__name__,
            )
        except Exception as e:
            logger.warning(
                "could not build notifier for --alert-on-cb (%s) — "
                "continuing without alerts", e,
            )

    if not handlers:
        return None
    if len(handlers) == 1:
        return handlers[0]
    return chain_handlers(*handlers)


async def run_worker(args: argparse.Namespace) -> int:
    queue = build_queue(settings)
    if isinstance(queue, NoOpPendingOrderQueue):
        logger.error(
            "worker: queue is NoOp (no DATABASE_URL or SUPABASE_URL+KEY in env). "
            "There's nothing to process. Exiting.",
        )
        return 1

    # Round 24: dispatchers are looked up via the registry. Default
    # registry wires shadow/paper → LogOnly, notify → NotifyOnly, leaves
    # `live` unregistered until Phase F.1 adds OKXLiveDispatcher.
    registry = build_default_registry(settings)
    try:
        dispatcher = registry.build(args.mode)
    except UnsupportedModeError as e:
        logger.error(
            "worker: %s — exiting. Phase F.1 will add the live dispatcher.",
            e,
        )
        return 1

    pipeline = None
    context_provider = None
    side_effect_handler = None
    if args.with_guards:
        pipeline, context_provider = _build_guard_pipeline(args)
        side_effect_handler = _build_side_effects(args)

    worker = PendingOrderWorker(
        queue, dispatcher,
        idle_sleep_sec=args.idle_sleep,
        risk_pipeline=pipeline,
        context_provider=context_provider,
        side_effect_handler=side_effect_handler,
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

    # Round 38: optional background sweeper sidecar
    sweeper_task: asyncio.Task | None = None
    if args.sweep_interval_sec > 0:
        if args.sweep_pending_max_age <= 0 and args.sweep_dispatching_max_age <= 0:
            logger.warning(
                "--sweep-interval-sec set but both age thresholds are 0 — "
                "sweeper will run but never expire anything. Pass at least "
                "--sweep-pending-max-age or --sweep-dispatching-max-age.",
            )
        sweeper_task = asyncio.create_task(background_sweep_loop(
            queue, stop,
            interval_sec=args.sweep_interval_sec,
            pending_max_age_sec=args.sweep_pending_max_age,
            dispatching_max_age_sec=args.sweep_dispatching_max_age,
        ))

    await worker.run_forever(stop)
    if sweeper_task is not None:
        # stop_event is set; sweeper exits between sweeps. Await its
        # final stats so the log line matches reality.
        try:
            sweep_stats = await asyncio.wait_for(sweeper_task, timeout=5.0)
            logger.info(
                "sweeper final: iterations=%d total_expired=%d errors=%d",
                sweep_stats.iterations, sweep_stats.total_expired,
                sweep_stats.errors,
            )
        except asyncio.TimeoutError:
            logger.warning("sweeper task did not exit within 5s; cancelling")
            sweeper_task.cancel()
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
