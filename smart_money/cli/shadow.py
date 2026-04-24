"""Shadow mode daemon — Phase 4.

P4a: WS listener + REST reconciler → RawFillEvent queue.
P4b (current): classifier turns RawFillEvent → Signal, persists position state.
P4c (pending): aggregator + shadow simulator.

Run:
    python -m smart_money.cli.shadow [--whitelist path/to/override.yaml]

Addresses to subscribe come from the dynamic whitelist:
    1. latest sm_rankings top N (config: ranking.whitelist_size)
    2. + manual include (from --whitelist yaml)
    3. - manual exclude, stale-no-fills, warmup-demoted

Graceful shutdown on SIGINT/SIGTERM. Exit codes: 0=clean, 1=no wallets,
2=WS failed to connect, 3=store init failed.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from uuid import UUID

from smart_money.config import settings
from smart_money.scanner.reconciler import FillsReconciler
from smart_money.scanner.realtime import HLFillsListener
from smart_money.signals.classifier import classify
from smart_money.signals.types import RawFillEvent
from smart_money.signals.whitelist import (
    WhitelistEntry,
    build_whitelist,
    load_manual_override,
)
from smart_money.store.db import TradeStore, build_store

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m smart_money.cli.shadow",
        description="Shadow mode daemon — subscribe to HL WS, classify fills, log signals.",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=Path("config/smart_money/whitelist_manual.yaml"),
        help="Manual include/exclude override YAML (default: config/smart_money/whitelist_manual.yaml).",
    )
    parser.add_argument(
        "--address",
        action="append",
        default=[],
        help="Force subscribe this address (repeatable). Bypasses ranking lookup.",
    )
    parser.add_argument(
        "--whitelist-size", type=int, default=settings.ranking.whitelist_size,
        help=f"Top N from latest ranking (default {settings.ranking.whitelist_size}).",
    )
    parser.add_argument(
        "--freshness-days", type=int, default=14,
        help="Wallets with no fill in this window are demoted to watch-only.",
    )
    parser.add_argument(
        "--warmup-hours", type=int, default=None,
        help="New wallets (first_seen within this window) demoted to watch-only.",
    )
    parser.add_argument(
        "--reconciler-interval", type=int, default=60,
        help="Seconds between REST reconciler sweeps.",
    )
    parser.add_argument(
        "--reconciler-lookback", type=int, default=300,
        help="Reconciler lookback window in seconds.",
    )
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def _resolve_whitelist(
    args: argparse.Namespace,
    store: TradeStore,
) -> list[WhitelistEntry]:
    """Build the active whitelist from CLI args + store state."""
    # --address overrides: go direct without ranking logic.
    if args.address:
        entries: list[WhitelistEntry] = []
        for addr in args.address:
            w = store.get_wallet_by_address(addr.lower())
            if w is None:
                logger.warning("--address %s not in sm_wallets — skip", addr[:10])
                continue
            entries.append(WhitelistEntry(
                wallet_id=w.id, address=w.address, score=0.0, rank=None,
                source="manual_include", is_tradeable=True, demotion_reason="none",
            ))
        return entries

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    override = load_manual_override(args.whitelist)

    warmup_cutoff = None
    if args.warmup_hours is not None:
        warmup_cutoff = now - timedelta(hours=args.warmup_hours)

    return build_whitelist(
        store,
        as_of=now,
        whitelist_size=args.whitelist_size,
        freshness_days=args.freshness_days,
        override=override,
        warmup_cutoff=warmup_cutoff,
    )


async def _drain_and_classify(
    queue: asyncio.Queue[RawFillEvent],
    store: TradeStore,
    address_to_wallet: dict[str, tuple[UUID, float]],
    stop: asyncio.Event,
) -> None:
    """Consume RawFillEvents, run classifier, persist position + signal/skip.

    P4b: log Signal events for now; P4c will pipe them into the aggregator.
    """
    while not stop.is_set():
        try:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        info = address_to_wallet.get(event.wallet_address.lower())
        if info is None:
            # Fill arrived for an address not on our current whitelist — skip.
            logger.debug(
                "drain: fill for non-whitelisted wallet %s — ignoring",
                event.wallet_address[:10],
            )
            continue
        wallet_id, wallet_score = info

        prev = store.get_position(wallet_id, event.symbol_hl)
        result = classify(event, prev=prev, wallet_id=wallet_id, wallet_score=wallet_score)

        # Persist the updated position (even on skip, the state may be 'flat'
        # representing cold-start — persisting lets later fills use it as prev).
        store.upsert_position(result.new_position)

        if result.skipped is not None:
            store.record_skipped_signal(result.skipped)
            logger.info(
                "skip wallet=%s sym=%s reason=%s dir=%r tid=%d",
                event.wallet_address[:10], event.symbol_hl,
                result.skipped.reason, event.direction_raw, event.hl_trade_id,
            )
            continue

        sig = result.signal
        assert sig is not None  # classify always returns one or the other
        logger.info(
            "signal wallet=%s sym=%s type=%s size=%s→%s px=%s lat_ms=%d src=%s",
            event.wallet_address[:10], event.symbol_hl, sig.signal_type.value,
            round(sig.size_delta, 4), round(sig.new_size, 4), sig.px,
            sig.total_latency_ms, event.source,
        )


async def run_shadow_daemon(args: argparse.Namespace) -> int:
    """Main async entry. Returns exit code."""
    try:
        store = build_store(settings)
    except Exception as e:
        logger.error("store init failed: %s", e)
        return 3

    whitelist = _resolve_whitelist(args, store)
    if not whitelist:
        logger.error("whitelist is empty — nothing to subscribe")
        return 1

    tradeable = [e for e in whitelist if e.is_tradeable]
    watch_only = [e for e in whitelist if not e.is_tradeable]
    logger.info(
        "shadow daemon: %d tradeable, %d watch-only",
        len(tradeable), len(watch_only),
    )
    for e in watch_only:
        logger.info(
            "  watch-only: %s rank=%s reason=%s",
            e.address[:10], e.rank, e.demotion_reason,
        )

    # Subscribe ALL (tradeable + watch-only) — state machine still tracks
    # non-tradeable wallets so we can detect recovery.
    addresses = [e.address for e in whitelist]
    address_to_wallet = {
        e.address.lower(): (e.wallet_id, e.score) for e in whitelist
    }

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[RawFillEvent] = asyncio.Queue()

    # Shared dedup set between WS listener and reconciler
    seen_tids: set[int] = set()

    def _mark_seen(ev: RawFillEvent) -> None:
        seen_tids.add(ev.hl_trade_id)

    listener = HLFillsListener(addresses, event_queue, loop, on_dispatch=_mark_seen)
    await listener.start()

    if listener._info is None:
        logger.error("WS listener failed to connect; aborting")
        return 2

    reconciler = FillsReconciler(
        addresses, listener._info, event_queue,
        interval_sec=args.reconciler_interval,
        lookback_sec=args.reconciler_lookback,
        seen_tids=seen_tids,
    )
    reconciler_task = asyncio.create_task(reconciler.run())

    stop = asyncio.Event()

    def _on_signal() -> None:
        logger.info("shutdown signal received")
        stop.set()

    for sig_ in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig_, _on_signal)

    drain_task = asyncio.create_task(
        _drain_and_classify(event_queue, store, address_to_wallet, stop)
    )

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
