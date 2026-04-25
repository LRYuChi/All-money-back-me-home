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
from execution.pending_orders.events import (
    EventLogger,
    InMemoryEventLogger,
    NoOpEventLogger,
    OrderEvent,
    PostgresEventLogger,
    SupabaseEventLogger,
    build_event_logger,
)
from execution.pending_orders.sweep_runner import (
    SweepStats,
    background_sweep_loop,
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
from execution.pending_orders.worker import (
    DispatchResult,
    Dispatcher,
    LogOnlyDispatcher,
    PendingOrderWorker,
)
from execution.pending_orders.registry import (
    DispatcherFactory,
    DispatcherRegistry,
    NotifyOnlyDispatcher,
    UnsupportedModeError,
    build_default_registry,
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
    # event logger (round 36)
    "EventLogger",
    "OrderEvent",
    "NoOpEventLogger",
    "InMemoryEventLogger",
    "PostgresEventLogger",
    "SupabaseEventLogger",
    "build_event_logger",
    # background sweeper (round 38)
    "SweepStats",
    "background_sweep_loop",
    # dispatcher (intent → pending row)
    "intent_to_pending",
    "make_intent_callback",
    # worker (queue → exchange)
    "DispatchResult",
    "Dispatcher",
    "LogOnlyDispatcher",
    "PendingOrderWorker",
    # dispatcher registry (mode → factory)
    "DispatcherFactory",
    "DispatcherRegistry",
    "NotifyOnlyDispatcher",
    "UnsupportedModeError",
    "build_default_registry",
]
