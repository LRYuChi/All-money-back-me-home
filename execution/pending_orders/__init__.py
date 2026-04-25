"""Pending orders middleware — StrategyIntent → durable queue → exchange.

API:
    from execution.pending_orders import (
        PendingOrder, PendingOrderStatus,
        PendingOrderQueue, build_queue,
        intent_to_pending, make_intent_callback,
    )
"""

from execution.pending_orders.types import (
    OrderSide,
    PendingOrder,
    PendingOrderStatus,
)
from execution.pending_orders.queue import (
    InMemoryPendingOrderQueue,
    NoOpPendingOrderQueue,
    PendingOrderNotFound,
    PendingOrderQueue,
    PostgresPendingOrderQueue,
    SupabasePendingOrderQueue,
    build_queue,
)
from execution.pending_orders.dispatcher import (
    intent_to_pending,
    make_intent_callback,
)

__all__ = [
    # types
    "OrderSide",
    "PendingOrder",
    "PendingOrderStatus",
    # queue
    "InMemoryPendingOrderQueue",
    "NoOpPendingOrderQueue",
    "PendingOrderNotFound",
    "PendingOrderQueue",
    "PostgresPendingOrderQueue",
    "SupabasePendingOrderQueue",
    "build_queue",
    # dispatcher
    "intent_to_pending",
    "make_intent_callback",
]
