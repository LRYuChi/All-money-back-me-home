"""PendingOrderWorker — claim from queue → guards → dispatch → mark terminal.

Architecture:
  - Worker calls `queue.claim_next_pending(mode)` in a loop
  - Optional `risk_pipeline` (GuardPipeline) checks/scales the order
  - Surviving orders are passed to the matching `Dispatcher`
  - Worker updates queue with terminal status + last_error

Pipeline integration (round 19):
  - `risk_pipeline=None` → no guards, direct dispatch
  - `risk_pipeline` set + `context_provider` set → call
    pipeline.evaluate(order, ctx) before dispatch:
      DENY → mark REJECTED with `guard:<name>` + reason
      SCALE → use updated notional in subsequent dispatch
  - context_provider receives the order so it can look up live exposures
    keyed by strategy_id / market

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
from typing import Any, Callable, Protocol

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


# Caller's hook to populate the GuardContext for an incoming order.
# Returns risk.guards.GuardContext but typed as Any here to avoid hard
# dependency on risk/ when guards aren't being used.
ContextProvider = Callable[[PendingOrder], Any]

# Caller's hook fired after a guard DENIES an order (round 26). Takes the
# order + the full list of GuardDecision objects from the pipeline run.
# Use this to disable strategies, fan out alerts, etc. Exceptions are
# logged and swallowed — never let a buggy handler crash the worker.
GuardSideEffectHandler = Callable[[PendingOrder, list], None]


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
        risk_pipeline: Any = None,                    # risk.guards.GuardPipeline | None
        context_provider: ContextProvider | None = None,
        side_effect_handler: GuardSideEffectHandler | None = None,
    ) -> None:
        self._queue = queue
        self._dispatcher = dispatcher
        self._idle_sleep = idle_sleep_sec
        self._risk_pipeline = risk_pipeline
        self._context_provider = context_provider
        self._side_effect_handler = side_effect_handler
        # Sanity: pipeline without context_provider can't run — fail loudly
        if risk_pipeline is not None and context_provider is None:
            raise ValueError(
                "risk_pipeline requires context_provider — pipeline needs "
                "a GuardContext per order to evaluate exposure / latency",
            )
        # side_effect_handler without a pipeline is useless — flag it
        if side_effect_handler is not None and risk_pipeline is None:
            raise ValueError(
                "side_effect_handler requires risk_pipeline — handler is "
                "only invoked on guard DENY, which can't happen without guards",
            )
        self._stats = {
            "claimed": 0,
            "filled": 0,
            "rejected": 0,
            "cancelled": 0,
            "partially_filled": 0,
            "dispatcher_errors": 0,
            "guard_denies": 0,
            "guard_scales": 0,
            "side_effects_invoked": 0,
            "side_effect_errors": 0,
        }

    @property
    def mode(self) -> ExecutionMode:
        return self._dispatcher.mode

    def process_one(self) -> int:
        """Claim → guards → dispatch one order. Returns 1 if processed, 0 if queue empty."""
        order = self._queue.claim_next_pending(self._dispatcher.mode)
        if order is None:
            return 0

        self._stats["claimed"] += 1

        # Pipeline check (round 19)
        if self._risk_pipeline is not None:
            blocked = self._run_guards(order)
            if blocked:
                return 1

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

    def _run_guards(self, order: PendingOrder) -> bool:
        """Returns True if pipeline blocked the order (REJECTED), False
        if it allowed/scaled and dispatch should proceed.

        Caller has already incremented `claimed`. On DENY this method
        increments `guard_denies` + `rejected` and writes status.
        On SCALE: increments `guard_scales` (order.target_notional_usd
        already mutated by pipeline)."""
        try:
            ctx = self._context_provider(order)
            run = self._risk_pipeline.evaluate(order, ctx)
        except Exception as e:
            # Treat pipeline crashes as DENY to fail safe
            logger.exception(
                "guard pipeline crashed on order id=%d: %s — treating as REJECTED",
                order.id, e,
            )
            try:
                self._queue.update_status(
                    order.id, PendingOrderStatus.REJECTED,
                    last_error=f"guard_pipeline_error: {type(e).__name__}: {e}",
                )
            except PendingOrderNotFound:
                pass
            self._stats["rejected"] += 1
            self._stats["guard_denies"] += 1
            return True

        # Detect any SCALE for stats
        for d in run.decisions:
            if d.result.value == "scale":
                self._stats["guard_scales"] += 1

        if not run.accepted:
            denying = next(
                (d for d in run.decisions if d.result.value == "deny"), None,
            )
            reason = (
                f"guard:{denying.guard_name}: {denying.reason}"
                if denying else "guard:unknown_deny"
            )
            try:
                self._queue.update_status(
                    order.id, PendingOrderStatus.REJECTED, last_error=reason,
                )
            except PendingOrderNotFound:
                logger.warning("order id=%d disappeared after DENY", order.id)
            self._stats["rejected"] += 1
            self._stats["guard_denies"] += 1
            logger.info("guard DENY id=%d → %s", order.id, reason)
            self._invoke_side_effect(order, list(run.decisions))
            return True

        return False

    def _invoke_side_effect(self, order: PendingOrder, decisions: list) -> None:
        """Fire post-DENY hook (round 26). Exceptions are logged + swallowed
        — handler bugs must not crash the worker. Pipeline-crash REJECTs
        intentionally do NOT fire this (no real decision to react to)."""
        if self._side_effect_handler is None:
            return
        try:
            self._side_effect_handler(order, decisions)
            self._stats["side_effects_invoked"] += 1
        except Exception as e:
            self._stats["side_effect_errors"] += 1
            logger.exception(
                "side_effect_handler raised on order id=%s: %s — ignoring",
                order.id, e,
            )

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
    "ContextProvider",
    "DispatchResult",
    "Dispatcher",
    "GuardSideEffectHandler",
    "LogOnlyDispatcher",
    "PendingOrderWorker",
]
