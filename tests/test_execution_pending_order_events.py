"""Tests for EventLogger + queue integration (round 36)."""
from __future__ import annotations

import pytest

from execution.pending_orders import (
    InMemoryEventLogger,
    InMemoryPendingOrderQueue,
    NoOpEventLogger,
    OrderEvent,
    PendingOrder,
    PendingOrderStatus,
    PostgresEventLogger,
    SupabaseEventLogger,
    build_event_logger,
)
from execution.pending_orders.events import (
    InMemoryEventLogger as DirectInMem,
    _coerce_status,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(notional=500.0, mode="shadow") -> PendingOrder:
    return PendingOrder(
        strategy_id="s1", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=notional, mode=mode,
    )


# ================================================================== #
# OrderEvent + _coerce_status
# ================================================================== #
def test_coerce_status_passes_enum_through():
    assert _coerce_status(PendingOrderStatus.PENDING) is PendingOrderStatus.PENDING


def test_coerce_status_parses_string():
    assert _coerce_status("filled") is PendingOrderStatus.FILLED


def test_coerce_status_rejects_unknown():
    with pytest.raises(ValueError):
        _coerce_status("ghost_status")


# ================================================================== #
# NoOpEventLogger
# ================================================================== #
def test_noop_silently_accepts_record():
    h = NoOpEventLogger()
    h.record(1, None, PendingOrderStatus.PENDING)
    h.record(1, PendingOrderStatus.PENDING, PendingOrderStatus.FILLED, reason="x")
    assert h.history(1) == []


# ================================================================== #
# InMemoryEventLogger
# ================================================================== #
def test_inmem_records_event_with_all_fields():
    h = InMemoryEventLogger()
    h.record(
        42, PendingOrderStatus.PENDING, PendingOrderStatus.FILLED,
        reason="happy path", detail={"price": 50_000},
    )
    e = h.events[0]
    assert isinstance(e, OrderEvent)
    assert e.order_id == 42
    assert e.from_status == PendingOrderStatus.PENDING
    assert e.to_status == PendingOrderStatus.FILLED
    assert e.reason == "happy path"
    assert e.detail == {"price": 50_000}
    assert e.created_at.tzinfo is not None


def test_inmem_initial_enqueue_has_none_from_status():
    h = InMemoryEventLogger()
    h.record(7, None, PendingOrderStatus.PENDING, reason="initial enqueue")
    assert h.events[0].from_status is None


def test_inmem_history_filters_by_order_id():
    h = InMemoryEventLogger()
    h.record(1, None, PendingOrderStatus.PENDING)
    h.record(2, None, PendingOrderStatus.PENDING)
    h.record(1, PendingOrderStatus.PENDING, PendingOrderStatus.FILLED)
    hist = h.history(1)
    assert len(hist) == 2
    assert all(e.order_id == 1 for e in hist)


def test_inmem_history_newest_first():
    from time import sleep
    h = InMemoryEventLogger()
    h.record(1, None, PendingOrderStatus.PENDING)
    sleep(0.001)
    h.record(1, PendingOrderStatus.PENDING, PendingOrderStatus.FILLED)
    hist = h.history(1)
    assert hist[0].to_status == PendingOrderStatus.FILLED
    assert hist[1].to_status == PendingOrderStatus.PENDING


def test_inmem_history_respects_limit():
    h = InMemoryEventLogger()
    for _ in range(10):
        h.record(1, None, PendingOrderStatus.PENDING)
    assert len(h.history(1, limit=3)) == 3


def test_inmem_accepts_string_status():
    """Defensive: caller may pass raw str instead of enum."""
    h = InMemoryEventLogger()
    h.record(1, "pending", "filled")
    assert h.events[0].from_status == PendingOrderStatus.PENDING
    assert h.events[0].to_status == PendingOrderStatus.FILLED


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_no_db():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""
    assert isinstance(build_event_logger(S()), NoOpEventLogger)


def test_factory_postgres_when_dsn_set():
    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""
    assert isinstance(build_event_logger(S()), PostgresEventLogger)


# ================================================================== #
# Queue integration: enqueue → PENDING event with from_status=None
# ================================================================== #
def test_queue_enqueue_emits_initial_event():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    events = log.history(oid)
    assert len(events) == 1
    assert events[0].from_status is None
    assert events[0].to_status == PendingOrderStatus.PENDING
    assert "enqueue" in (events[0].reason or "")
    # detail captures order metadata for downstream debugging
    assert events[0].detail["strategy_id"] == "s1"
    assert events[0].detail["notional_usd"] == 500.0


def test_queue_idempotent_enqueue_does_not_duplicate_event():
    """Re-enqueue with same client_order_id returns prior id, no extra event."""
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    o1 = make_order()
    o1.client_order_id = "coid-fixed"
    q.enqueue(o1)

    o2 = make_order()
    o2.client_order_id = "coid-fixed"
    q.enqueue(o2)

    # Only the first enqueue produced an event
    assert len(log.events) == 1


# ================================================================== #
# Queue integration: claim → DISPATCHING event
# ================================================================== #
def test_queue_claim_emits_dispatching_event():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    log.events.clear()   # ignore initial enqueue event

    claimed = q.claim_next_pending("shadow")
    assert claimed is not None
    events = log.history(oid)
    assert len(events) == 1
    assert events[0].from_status == PendingOrderStatus.PENDING
    assert events[0].to_status == PendingOrderStatus.DISPATCHING
    assert events[0].detail["attempt"] == 1


def test_queue_claim_no_match_emits_no_event():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    q.enqueue(make_order(mode="shadow"))
    log.events.clear()

    result = q.claim_next_pending("live")   # different mode
    assert result is None
    assert log.events == []


# ================================================================== #
# Queue integration: update_status → status transition event
# ================================================================== #
def test_update_status_emits_transition_event():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    q.claim_next_pending("shadow")
    log.events.clear()

    q.update_status(oid, PendingOrderStatus.FILLED)
    events = log.history(oid)
    assert len(events) == 1
    assert events[0].from_status == PendingOrderStatus.DISPATCHING
    assert events[0].to_status == PendingOrderStatus.FILLED


def test_update_status_passes_last_error_as_reason():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    log.events.clear()

    q.update_status(oid, PendingOrderStatus.REJECTED,
                    last_error="guard:min_size: notional $5 below min $10")
    e = log.history(oid)[0]
    assert e.to_status == PendingOrderStatus.REJECTED
    assert "guard:min_size" in (e.reason or "")


def test_update_status_no_change_emits_no_event():
    """Setting status to its current value should not produce an event."""
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    log.events.clear()

    q.update_status(oid, PendingOrderStatus.PENDING)
    assert log.events == []


# ================================================================== #
# End-to-end: full order timeline
# ================================================================== #
def test_full_timeline_pending_dispatching_filled():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    q.claim_next_pending("shadow")
    q.update_status(oid, PendingOrderStatus.FILLED)

    history = log.history(oid)
    assert len(history) == 3
    # newest first
    transitions = [(e.from_status, e.to_status) for e in history]
    assert transitions == [
        (PendingOrderStatus.DISPATCHING, PendingOrderStatus.FILLED),
        (PendingOrderStatus.PENDING, PendingOrderStatus.DISPATCHING),
        (None, PendingOrderStatus.PENDING),
    ]


def test_full_timeline_with_rejection():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    oid = q.enqueue(make_order())
    q.claim_next_pending("shadow")
    q.update_status(oid, PendingOrderStatus.REJECTED,
                    last_error="guard:daily_loss_cb: -$600 ≤ -$500")

    history = log.history(oid)
    assert len(history) == 3
    rejection = history[0]
    assert rejection.to_status == PendingOrderStatus.REJECTED
    assert "daily_loss_cb" in (rejection.reason or "")


# ================================================================== #
# Robustness: event-logger failures don't break the queue
# ================================================================== #
def test_queue_swallows_event_logger_exception():
    """Critical contract: an audit-table outage MUST NOT crash trading."""
    class BoomLogger:
        def record(self, *a, **kw):
            raise ConnectionError("audit DB down")
        def history(self, *a, **kw):
            return []

    q = InMemoryPendingOrderQueue(event_logger=BoomLogger())
    # All three operations must succeed despite the logger exploding
    oid = q.enqueue(make_order())
    claimed = q.claim_next_pending("shadow")
    q.update_status(oid, PendingOrderStatus.FILLED)
    assert claimed is not None
    assert q.get(oid).status == PendingOrderStatus.FILLED


# ================================================================== #
# Default behaviour: NoOp when no logger passed
# ================================================================== #
def test_queue_default_logger_is_noop():
    """Backwards compat: existing code that constructed
    InMemoryPendingOrderQueue() with no arg keeps working."""
    q = InMemoryPendingOrderQueue()
    oid = q.enqueue(make_order())
    q.claim_next_pending("shadow")
    q.update_status(oid, PendingOrderStatus.FILLED)
    # No errors, order in expected terminal state
    assert q.get(oid).status == PendingOrderStatus.FILLED
