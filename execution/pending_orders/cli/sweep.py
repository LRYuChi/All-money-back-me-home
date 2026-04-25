"""CLI: sweep stuck PENDING/DISPATCHING orders to EXPIRED (round 37).

Usage (one-shot, cron-friendly):
    python -m execution.pending_orders.cli.sweep \\
        --pending-max-age 300 --dispatching-max-age 60

Usage (loop, sleeps between sweeps):
    python -m execution.pending_orders.cli.sweep \\
        --pending-max-age 300 --interval 30

Recommended cron: every 1 min for short-horizon strategies, every 5 min
for daily-tier. Each sweep emits a `pending_order_events` row per expired
order with the threshold + actual age recorded — searchable in dashboard.

Setting either threshold to 0 disables that bucket. The default of
0 + 0 sweeps NOTHING and exits with a warning — caller MUST opt in
to which bucket(s) to sweep.

Exit codes:
    0  — clean (n orders swept, n could be 0)
    1  — IO setup failure (queue not configured)
    2  — invalid args (e.g. both thresholds 0)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from execution.pending_orders import (
    NoOpPendingOrderQueue,
    build_queue,
)
from smart_money.config import settings

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m execution.pending_orders.cli.sweep",
        description="Move stuck PENDING/DISPATCHING orders to EXPIRED.",
    )
    p.add_argument(
        "--pending-max-age", type=float, default=0,
        help="Move PENDING older than N seconds → EXPIRED. 0 = disabled.",
    )
    p.add_argument(
        "--dispatching-max-age", type=float, default=0,
        help="Move DISPATCHING older than N seconds → EXPIRED (worker probably "
             "crashed mid-dispatch). 0 = disabled.",
    )
    p.add_argument(
        "--interval", type=float, default=0,
        help="If > 0, sleep N seconds between sweeps and loop. 0 = one-shot.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return p


def _sweep_once(queue, pending_max: float, dispatching_max: float) -> int:
    n = queue.sweep_expired(
        pending_max_age_sec=pending_max,
        dispatching_max_age_sec=dispatching_max,
    )
    if n > 0:
        logger.info("sweep: expired %d order(s)", n)
    else:
        logger.debug("sweep: nothing to expire")
    return n


async def _run_loop(queue, args: argparse.Namespace) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, _on_signal)

    total = 0
    while not stop.is_set():
        try:
            total += _sweep_once(
                queue, args.pending_max_age, args.dispatching_max_age,
            )
        except Exception as e:
            logger.exception("sweep iteration failed: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=args.interval)
        except asyncio.TimeoutError:
            pass
    logger.info("sweep loop stopped: total expired = %d", total)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.pending_max_age <= 0 and args.dispatching_max_age <= 0:
        logger.error(
            "sweep: both --pending-max-age and --dispatching-max-age are 0; "
            "nothing to do. Pass at least one threshold > 0.",
        )
        return 2

    queue = build_queue(settings)
    if isinstance(queue, NoOpPendingOrderQueue):
        logger.error(
            "sweep: queue is NoOp (no DB configured); exiting.",
        )
        return 1

    if args.interval > 0:
        return asyncio.run(_run_loop(queue, args))

    n = _sweep_once(queue, args.pending_max_age, args.dispatching_max_age)
    logger.info("sweep one-shot: expired %d", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
