"""Background SUBMITTED-state poller (round 42).

After round 41's ccxt-okx wiring, OKX's place_order can return ACCEPTED
(limit order resting on the book, market order accepted but not yet
filled). We mark such orders SUBMITTED and rely on a poller to advance
them to a terminal state (FILLED / PARTIALLY_FILLED / REJECTED /
CANCELLED) over time.

This module mirrors the sweep_runner pattern:
  - async background_poll_submitted_loop(queue, dispatcher, stop_event,
    interval_sec) — runs until stop_event fires
  - PollStats dataclass with iterations / polled / advanced / errors
  - Per-order failures are caught + counted, loop continues
  - asyncio.wait_for(stop.wait(), interval) lets the loop exit promptly

The dispatcher must implement `fetch_status(order) -> DispatchResult | None`.
LogOnlyDispatcher / NotifyOnlyDispatcher don't (their orders never sit
in SUBMITTED), so the poller checks via getattr and exits early when
absent.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from execution.pending_orders.types import PendingOrderStatus

logger = logging.getLogger(__name__)


@dataclass
class PollStats:
    """Per-loop counters; caller can inspect after stop."""
    iterations: int = 0
    orders_polled: int = 0
    orders_advanced: int = 0    # transitioned to a different status
    errors: int = 0


async def background_poll_submitted_loop(
    queue,                                # PendingOrderQueue
    dispatcher,                           # OKXLiveDispatcher / similar
    stop_event: asyncio.Event,
    *,
    interval_sec: float,
    max_orders_per_iteration: int = 100,
) -> PollStats:
    """Run a poll cycle every `interval_sec` until stop_event fires.

    Each iteration:
      1. queue.list_recent(limit, status=SUBMITTED)
      2. for each: dispatcher.fetch_status(order) → maybe DispatchResult
      3. if DispatchResult.terminal_status differs from SUBMITTED, call
         queue.update_status to advance the order

    Validation:
      - interval_sec > 0
      - dispatcher must implement fetch_status (else early-exit; logged)
    """
    if interval_sec <= 0:
        raise ValueError(f"interval_sec must be > 0; got {interval_sec}")
    if not hasattr(dispatcher, "fetch_status"):
        logger.warning(
            "background_poll_submitted_loop: dispatcher %s has no "
            "fetch_status method — sidecar exits without polling",
            type(dispatcher).__name__,
        )
        return PollStats()

    stats = PollStats()
    logger.info(
        "SUBMITTED poller starting: interval=%.1fs dispatcher=%s "
        "max_orders/iter=%d",
        interval_sec, type(dispatcher).__name__, max_orders_per_iteration,
    )

    while not stop_event.is_set():
        stats.iterations += 1
        try:
            await _poll_once(queue, dispatcher, stats, max_orders_per_iteration)
        except Exception as e:
            stats.errors += 1
            logger.exception(
                "SUBMITTED poller iteration %d failed: %s",
                stats.iterations, e,
            )

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            continue

    logger.info(
        "SUBMITTED poller stopped: iterations=%d polled=%d advanced=%d errors=%d",
        stats.iterations, stats.orders_polled, stats.orders_advanced,
        stats.errors,
    )
    return stats


async def _poll_once(
    queue, dispatcher, stats: PollStats, max_orders: int,
) -> None:
    """One poll iteration. Async wrapper around sync queue + dispatcher
    calls — they run inline; if a deployment needs offload to a thread
    pool, wrap with run_in_executor here."""
    try:
        orders = queue.list_recent(
            limit=max_orders, status=PendingOrderStatus.SUBMITTED,
        )
    except Exception as e:
        stats.errors += 1
        logger.warning("poller: list_recent SUBMITTED failed: %s", e)
        return

    for order in orders:
        # Concurrency note: another worker / human action might have
        # advanced this order between list and update — that's OK because
        # update_status writes regardless and the audit logger captures
        # both transitions. We optionally skip via a guard:
        if order.status != PendingOrderStatus.SUBMITTED:
            continue

        stats.orders_polled += 1
        try:
            result = dispatcher.fetch_status(order)
        except Exception as e:
            stats.errors += 1
            logger.warning(
                "poller: fetch_status raised on id=%s: %s", order.id, e,
            )
            continue

        if result is None:
            # Dispatcher couldn't poll (e.g. missing client_order_id) —
            # leave the order alone; ops can investigate
            continue

        new_status = result.terminal_status
        if new_status == PendingOrderStatus.SUBMITTED:
            # Still on the book; nothing to update
            continue

        try:
            queue.update_status(
                order.id, new_status, last_error=result.last_error,
            )
            stats.orders_advanced += 1
            logger.info(
                "poller: id=%s SUBMITTED → %s", order.id, new_status.value,
            )
        except Exception as e:
            stats.errors += 1
            logger.warning(
                "poller: update_status(%s, %s) failed: %s",
                order.id, new_status, e,
            )


__all__ = ["PollStats", "background_poll_submitted_loop"]
