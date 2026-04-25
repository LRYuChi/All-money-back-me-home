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
    p.add_argument("--log-level", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


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
    worker = PendingOrderWorker(queue, dispatcher, idle_sleep_sec=args.idle_sleep)

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
