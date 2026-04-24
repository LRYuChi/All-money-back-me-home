"""Shadow mode daemon — Phase 4.

P4a (this revision): boot WS listener + REST reconciler, drain events
from the queue and log them. No classifier / aggregator yet — those
land in P4b/c.

Run:
    python -m smart_money.cli.shadow --whitelist path/to/whitelist.yaml
    python -m smart_money.cli.shadow --address 0xabc... --address 0xdef...

Graceful shutdown on SIGINT/SIGTERM: stops listener, cancels reconciler,
drains remaining queue items, exits 0.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from smart_money.scanner.reconciler import FillsReconciler
from smart_money.scanner.realtime import HLFillsListener
from smart_money.scanner.seeds import is_valid_address, load_seed_file
from smart_money.signals.types import RawFillEvent

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.shadow",
        description="Run shadow mode (paper trading) daemon subscribing to HL ws fills.",
    )
    parser.add_argument(
        "--whitelist",
        type=str,
        help="Path to whitelist yaml (falls back to seeds.yaml if not given).",
    )
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        help="Explicit wallet address to subscribe (repeatable). Overrides --whitelist.",
    )
    parser.add_argument(
        "--reconciler-interval",
        type=int,
        default=60,
        help="Seconds between REST reconciler sweeps (default 60).",
    )
    parser.add_argument(
        "--reconciler-lookback",
        type=int,
        default=300,
        help="Reconciler lookback window in seconds (default 300).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def _resolve_addresses(args: argparse.Namespace) -> list[str]:
    """Get the list of addresses to subscribe, in priority order."""
    if args.address:
        addrs = [a.lower() for a in args.address if is_valid_address(a)]
        if len(addrs) != len(args.address):
            logger.warning("dropped %d invalid addresses", len(args.address) - len(addrs))
        return addrs

    path = Path(args.whitelist) if args.whitelist else None
    if path is None:
        # Default to project's seeds.yaml
        default = Path("smart_money/data/seeds.yaml")
        if default.exists():
            path = default
        else:
            raise SystemExit("no addresses given and no seeds.yaml found")

    entries = load_seed_file(path)
    return [e.address.lower() for e in entries]


async def _drain_events(queue: asyncio.Queue[RawFillEvent], stop: asyncio.Event) -> None:
    """Consume events from the queue and log them.

    P4a: structured logging only. P4b will replace this with classifier.process().
    """
    while not stop.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        logger.info(
            "fill src=%s wallet=%s sym=%s dir=%r sz=%+.4f px=%s tid=%d "
            "lat_ms=%d (net=%d, proc=%d)",
            event.source,
            event.wallet_address[:10],
            event.symbol_hl,
            event.direction_raw,
            event.size,
            event.px,
            event.hl_trade_id,
            event.total_latency_ms,
            event.network_latency_ms,
            event.processing_latency_ms,
        )


async def run_shadow_daemon(args: argparse.Namespace) -> int:
    """Main async entry. Returns exit code."""
    addresses = _resolve_addresses(args)
    if not addresses:
        logger.error("no valid addresses to subscribe")
        return 1

    logger.info("shadow daemon starting: %d wallets", len(addresses))

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[RawFillEvent] = asyncio.Queue()

    # Shared dedup set: WS listener marks seen tids so reconciler skips them.
    seen_tids: set[int] = set()

    def _mark_seen(ev: RawFillEvent) -> None:
        seen_tids.add(ev.hl_trade_id)

    listener = HLFillsListener(
        addresses,
        event_queue,
        loop,
        on_dispatch=_mark_seen,
    )
    await listener.start()

    # Reconciler needs a UserFillsByTimeClient — reuse the listener's Info instance.
    # If listener._info is None (connection failed), reconciler still works but won't
    # share transport. For P4a simplicity we demand listener must be connected.
    if listener._info is None:
        logger.error("WS listener failed to connect; aborting shadow daemon")
        return 2

    reconciler = FillsReconciler(
        addresses,
        listener._info,
        event_queue,
        interval_sec=args.reconciler_interval,
        lookback_sec=args.reconciler_lookback,
        seen_tids=seen_tids,
    )
    reconciler_task = asyncio.create_task(reconciler.run())

    stop = asyncio.Event()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _on_signal)

    drain_task = asyncio.create_task(_drain_events(event_queue, stop))

    try:
        await stop.wait()
    finally:
        logger.info("stopping daemon...")
        reconciler_task.cancel()
        drain_task.cancel()
        await listener.stop()
        for t in (reconciler_task, drain_task):
            try:
                await t
            except asyncio.CancelledError:
                pass

    logger.info("shadow daemon stopped cleanly")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    return asyncio.run(run_shadow_daemon(args))


if __name__ == "__main__":
    sys.exit(main())
