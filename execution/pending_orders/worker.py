"""PendingOrderWorker — claim from queue → dispatch → mark terminal.

Architecture:
  - Worker calls `queue.claim_next_pending(mode)` in a loop
  - Each claimed order is passed to the matching `Dispatcher` (per mode)
  - Dispatcher returns `DispatchResult` describing outcome
  - Worker updates queue with terminal status + last_error

Dispatcher Protocol is intentionally tiny — concrete dispatchers live
in execution/dispatchers/*.py per mode. Phase F.1 lands the OKX
LiveDispatcher; this round ships only LogOnlyDispatcher (shadow / notify
modes that don't actually trade) so the loop is closeable end-to-end.

Concurrency:
  - Single worker per process for now
  - Postgres queue's claim_next_pending uses SKIP LOCKED, so multiple
    worker processes can run safely (Phase H scale-out)
  - Workers MUST be idempotent: re-running a claimed order produces the
    same outcome (defended by client_order_id at exchange level — Phase F.1)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

from execution.pending_orders.queue import (
    PendingOrderNotFound,
    PendingOrderQueue,
)
from execution.pending_orders.types import (
    ExecutionMode,
    PendingOrder,
    PendingOrderStatus,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DispatchResult:
    """What happened when a dispatcher tried to act on an order.

    `terminal_status` is the new status to set: FILLED / REJECTED /
    CANCELLED / EXPIRED, OR PARTIALLY_FILLED to leave the order open
    for the worker to retry/poll later (Phase F.1 fill polling).

    `last_error` is required when terminal_status is REJECTED; otherwise
    optional. Used to populate pending_orders.last_error for ops triage.
    """

    terminal_status: PendingOrderStatus
    last_error: str | None = None
    detail: dict | None = None


class Dispatcher(Protocol):
    """Per-mode dispatcher. `mode` indicates which orders this handles."""

    @property
    def mode(self) -> ExecutionMode: ...
    def dispatch(self, order: PendingOrder) -> DispatchResult: ...


# ================================================================== #
# Built-in dispatchers
# ================================================================== #
class LogOnlyDispatcher:
    """For shadow/notify modes: log + mark FILLED. No exchange interaction.

    Useful for end-to-end testing the queue loop, plus genuinely-useful
    for `mode=notify` strategies that only want Telegram alerts (a
    separate notifier hook produces those — see Phase D Notifier).
    """

    def __init__(self, mode: ExecutionMode = "shadow") -> None:
        self._mode = mode

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    def dispatch(self, order: PendingOrder) -> DispatchResult:
        logger.info(
            "log-only dispatch: id=%d strategy=%s %s %s notional=%.2f mode=%s",
            order.id, order.strategy_id, order.symbol, order.side,
            order.target_notional_usd, order.mode,
        )
        return DispatchResult(
            terminal_status=PendingOrderStatus.FILLED,
            detail={"dispatcher": "log_only", "mode": self._mode},
        )


# ================================================================== #
# Worker
# ================================================================== #
class PendingOrderWorker:
    """Single-mode worker. Run one per ExecutionMode you want to service.

    Lifecycle:
        worker = PendingOrderWorker(queue, LogOnlyDispatcher("shadow"))
        await worker.run_forever(stop_event)   # async loop
        # or:
        n = worker.process_one()               # synchronous, returns 0 or 1
    """

    def __init__(
        self,
        queue: PendingOrderQueue,
        dispatcher: Dispatcher,
        *,
        idle_sleep_sec: float = 1.0,
    ) -> None:
        self._queue = queue
        self._dispatcher = dispatcher
        self._idle_sleep = idle_sleep_sec
        self._stats = {
            "claimed": 0,
            "filled": 0,
            "rejected": 0,
            "cancelled": 0,
            "partially_filled": 0,
            "dispatcher_errors": 0,
        }

    @property
    def mode(self) -> ExecutionMode:
        return self._dispatcher.mode

    def process_one(self) -> int:
        """Claim + dispatch one order. Returns 1 if processed, 0 if queue empty."""
        order = self._queue.claim_next_pending(self._dispatcher.mode)
        if order is None:
            return 0

        self._stats["claimed"] += 1

        try:
            result = self._dispatcher.dispatch(order)
        except Exception as e:
            self._stats["dispatcher_errors"] += 1
            logger.exception("dispatcher %s raised on order id=%d: %s",
                             type(self._dispatcher).__name__, order.id, e)
            # Mark REJECTED with the exception text. Worker doesn't auto-retry —
            # ops can re-enqueue manually (or strategy fires again next tick).
            try:
                self._queue.update_status(
                    order.id, PendingOrderStatus.REJECTED,
                    last_error=f"{type(e).__name__}: {e}",
                )
            except PendingOrderNotFound:
                logger.warning(
                    "order id=%d disappeared between claim + status update", order.id,
                )
            self._stats["rejected"] += 1
            return 1

        try:
            self._queue.update_status(
                order.id, result.terminal_status,
                last_error=result.last_error,
            )
        except PendingOrderNotFound:
            logger.warning(
                "order id=%d disappeared between dispatch + status update", order.id,
            )
            return 1

        # Stats
        s = result.terminal_status
        if s == PendingOrderStatus.FILLED:
            self._stats["filled"] += 1
        elif s == PendingOrderStatus.REJECTED:
            self._stats["rejected"] += 1
        elif s == PendingOrderStatus.CANCELLED:
            self._stats["cancelled"] += 1
        elif s == PendingOrderStatus.PARTIALLY_FILLED:
            self._stats["partially_filled"] += 1

        return 1

    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    def reset_stats(self) -> None:
        for k in self._stats:
            self._stats[k] = 0

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        """Async loop — claim and process until stop_event is set.

        Sleeps `idle_sleep_sec` between empty polls (avoids busy-loop).
        Worker shutdowns are graceful: in-flight dispatches complete
        before the loop exits.
        """
        logger.info(
            "PendingOrderWorker starting: mode=%s dispatcher=%s",
            self._dispatcher.mode, type(self._dispatcher).__name__,
        )
        while not stop_event.is_set():
            try:
                processed = self._process_one_safe()
            except Exception as e:
                # Belt+braces: process_one already swallows dispatcher errors,
                # but a bug in the worker itself shouldn't kill the loop
                logger.exception("worker loop unexpected error: %s", e)
                processed = 0

            if processed == 0:
                try:
                    await asyncio.sleep(self._idle_sleep)
                except asyncio.CancelledError:
                    break

    def _process_one_safe(self) -> int:
        """Wrap process_one with a final try/except so the run_forever loop
        is robust against any exception leak from process_one's bookkeeping."""
        try:
            return self.process_one()
        except Exception as e:
            logger.exception("process_one raised: %s", e)
            return 0


__all__ = [
    "DispatchResult",
    "Dispatcher",
    "LogOnlyDispatcher",
    "PendingOrderWorker",
]
