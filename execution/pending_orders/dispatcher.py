"""StrategyIntent → PendingOrder converter + IntentCallback factory.

Used by daemon to wire StrategyRuntime.on_intent → durable queue:

    queue = build_queue(settings)
    runtime = StrategyRuntime(
        ...,
        on_intent=make_intent_callback(
            queue, mode="shadow",
            deduper=WindowedIntentDeduper(window_sec=60),  # round 44
        ),
    )
"""
from __future__ import annotations

import logging
from typing import Callable

from execution.pending_orders.dedup import IntentDeduper, NoOpIntentDeduper
from execution.pending_orders.types import (
    ExecutionMode,
    PendingOrder,
    PendingOrderStatus,
)
from execution.pending_orders.queue import PendingOrderQueue
from shared.signals.types import Direction, StrategyIntent

logger = logging.getLogger(__name__)


def intent_to_pending(
    intent: StrategyIntent,
    mode: ExecutionMode,
    *,
    fused_signal_id: int | None = None,
    client_order_id: str | None = None,
) -> PendingOrder:
    """Pure converter: StrategyIntent + mode → PendingOrder.

    The Direction.NEUTRAL case is rejected — neutral intents shouldn't
    reach the queue (evaluator should not have emitted them as actionable).
    Caller should defend if it can happen in their system.
    """
    if intent.direction == Direction.NEUTRAL:
        raise ValueError(
            f"intent_to_pending: NEUTRAL intent for {intent.strategy_id} cannot be queued"
        )

    side = "long" if intent.direction == Direction.LONG else "short"

    # Auto-generate client_order_id when not provided. Deterministic format:
    #   sm-{strategy_id}-{symbol}-{side}-{intent_ts_ms}
    # Same intent re-submitted (e.g. worker restart) gets same id → queue
    # idempotency takes over and returns existing row.
    if client_order_id is None:
        ts_ms = int(intent.ts.timestamp() * 1000)
        # Sanitise symbol for id (replace non-ascii / slashes)
        sym_clean = intent.symbol.replace("/", "-").replace(":", "-")
        client_order_id = f"sm-{intent.strategy_id}-{sym_clean}-{side}-{ts_ms}"

    return PendingOrder(
        strategy_id=intent.strategy_id,
        symbol=intent.symbol,
        side=side,
        target_notional_usd=intent.target_notional_usd,
        entry_price_ref=intent.entry_price_ref,
        stop_loss_pct=intent.stop_loss_pct,
        take_profit_pct=intent.take_profit_pct,
        mode=mode,
        status=PendingOrderStatus.PENDING,
        fused_signal_id=fused_signal_id,
        client_order_id=client_order_id,
    )


def make_intent_callback(
    queue: PendingOrderQueue,
    mode: ExecutionMode,
    *,
    deduper: IntentDeduper | None = None,
) -> Callable[[StrategyIntent], None]:
    """Build a callback suitable for StrategyRuntime.on_intent.

    Failures (queue.enqueue raising) are logged but not propagated —
    StrategyRuntime swallows callback exceptions itself, but this layer
    adds context-rich logging before the runtime sees the exception.

    Mode is fixed per callback; if you need per-strategy mode routing
    (e.g. some strategies live, others shadow), build separate callbacks
    and pick at evaluation time. For Phase F.1 we keep it simple.

    Round 44: optional `deduper` skips intents that duplicate a recent
    one (same strategy + symbol + side within the dedup window). Without
    this, two near-simultaneous ticks open two positions because the
    deterministic coid uses intent_ts and they differ by ms.
    Default = NoOp (backwards compat).
    """
    deduper = deduper or NoOpIntentDeduper()

    def _cb(intent: StrategyIntent) -> None:
        if intent.direction == Direction.NEUTRAL:
            logger.warning(
                "intent_callback: skipping NEUTRAL intent for %s",
                intent.strategy_id,
            )
            return
        # Round 44: dedup BEFORE enqueue (deterministic coid is per-ts so
        # same coid won't help here)
        try:
            if deduper.is_duplicate(intent):
                logger.warning(
                    "intent_callback: SKIP duplicate intent strategy=%s "
                    "symbol=%s direction=%s ts=%s (dedup window hit)",
                    intent.strategy_id, intent.symbol,
                    intent.direction.value, intent.ts.isoformat(),
                )
                return
        except Exception as e:
            # Defensive: deduper failure should NOT block trading.
            # Log + treat as not-duplicate (fail-open, matches guard convention).
            logger.warning(
                "intent_callback: deduper.is_duplicate raised (%s) — "
                "treating as not-duplicate", e,
            )

        try:
            order = intent_to_pending(intent, mode=mode)
            new_id = queue.enqueue(order)
            logger.info(
                "pending_order enqueued id=%d strategy=%s %s %s notional=%.2f mode=%s",
                new_id, intent.strategy_id, intent.symbol,
                order.side, intent.target_notional_usd, mode,
            )
            # Record AFTER successful enqueue; failed enqueue shouldn't
            # consume the dedup slot (caller may retry the same intent).
            try:
                deduper.record(intent)
            except Exception as e:
                logger.warning(
                    "intent_callback: deduper.record raised (%s) — "
                    "ignored (record is best-effort)", e,
                )
        except Exception as e:
            # Re-raise so StrategyRuntime can count the error in stats.
            # (StrategyRuntime catches it — this is just to keep stats accurate.)
            logger.error(
                "intent_callback failed for %s: %s",
                intent.strategy_id, e,
            )
            raise

    return _cb


__all__ = ["intent_to_pending", "make_intent_callback"]
