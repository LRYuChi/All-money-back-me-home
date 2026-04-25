"""Tests for execution.pending_orders — types + queue + dispatcher."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from execution.pending_orders import (
    InMemoryPendingOrderQueue,
    NoOpPendingOrderQueue,
    PendingOrder,
    PendingOrderNotFound,
    PendingOrderStatus,
    build_queue,
    intent_to_pending,
    make_intent_callback,
)
from shared.signals.types import (
    Direction,
    FusedSignal,
    StrategyIntent,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_intent(
    strategy_id="test_v1",
    symbol="crypto:OKX:BTC/USDT:USDT",
    direction=Direction.LONG,
    target=500.0,
    sl=0.02,
    tp=None,
) -> StrategyIntent:
    fs = FusedSignal(
        symbol=symbol, horizon="15m", direction=direction,
        ensemble_score=0.7, regime="BULL_TRENDING",
        sources_count=2, contributions={"smart_money": 0.4}, conflict=False,
    )
    return StrategyIntent(
        strategy_id=strategy_id, symbol=symbol, direction=direction,
        target_notional_usd=target,
        entry_price_ref=50_000.0,
        stop_loss_pct=sl, take_profit_pct=tp,
        source_fused=fs,
    )


def make_order(**overrides) -> PendingOrder:
    base = dict(
        strategy_id="x", symbol="X", side="long",
        target_notional_usd=100.0, mode="shadow",
    )
    base.update(overrides)
    return PendingOrder(**base)


# ================================================================== #
# Status enum + terminal flag
# ================================================================== #
def test_terminal_flag_for_terminal_statuses():
    for s in [PendingOrderStatus.FILLED, PendingOrderStatus.REJECTED,
              PendingOrderStatus.CANCELLED, PendingOrderStatus.EXPIRED]:
        order = make_order(status=s)
        assert order.is_terminal is True


def test_non_terminal_statuses():
    for s in [PendingOrderStatus.PENDING, PendingOrderStatus.DISPATCHING,
              PendingOrderStatus.SUBMITTED, PendingOrderStatus.PARTIALLY_FILLED]:
        order = make_order(status=s)
        assert order.is_terminal is False


def test_to_row_serialises_status_value():
    order = make_order(status=PendingOrderStatus.SUBMITTED)
    row = order.to_row()
    assert row["status"] == "submitted"
    assert row["mode"] == "shadow"
    assert "created_at" in row


# ================================================================== #
# NoOp queue
# ================================================================== #
def test_noop_returns_zero_id():
    q = NoOpPendingOrderQueue()
    assert q.enqueue(make_order()) == 0


def test_noop_get_raises():
    q = NoOpPendingOrderQueue()
    with pytest.raises(PendingOrderNotFound):
        q.get(1)


def test_noop_claim_returns_none():
    q = NoOpPendingOrderQueue()
    assert q.claim_next_pending(mode="shadow") is None


# ================================================================== #
# InMemory queue — basic CRUD
# ================================================================== #
def test_inmemory_enqueue_assigns_incrementing_ids():
    q = InMemoryPendingOrderQueue()
    a, b = make_order(), make_order()
    id_a = q.enqueue(a)
    id_b = q.enqueue(b)
    assert id_a == 1 and id_b == 2
    assert a.id == 1


def test_inmemory_get_returns_order():
    q = InMemoryPendingOrderQueue()
    o = make_order(strategy_id="s1")
    q.enqueue(o)
    fetched = q.get(o.id)
    assert fetched.strategy_id == "s1"


def test_inmemory_get_unknown_raises():
    q = InMemoryPendingOrderQueue()
    with pytest.raises(PendingOrderNotFound):
        q.get(999)


# ================================================================== #
# Idempotency: same client_order_id → return existing id
# ================================================================== #
def test_inmemory_idempotency_same_client_order_id():
    q = InMemoryPendingOrderQueue()
    a = make_order(client_order_id="cloid-abc")
    id_a = q.enqueue(a)
    # Re-enqueue logically-identical order with same client_order_id
    b = make_order(client_order_id="cloid-abc", target_notional_usd=999)
    id_b = q.enqueue(b)
    # Returns existing id, no new row inserted
    assert id_b == id_a
    assert len(q.list_recent()) == 1


def test_inmemory_no_dedup_when_no_client_order_id():
    """Two orders without client_order_id are independent rows."""
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order())
    q.enqueue(make_order())
    assert len(q.list_recent()) == 2


# ================================================================== #
# claim_next_pending FIFO + status transition
# ================================================================== #
def test_claim_next_pending_picks_oldest_first():
    q = InMemoryPendingOrderQueue()
    first = make_order(strategy_id="first")
    second = make_order(strategy_id="second")
    q.enqueue(first)
    q.enqueue(second)

    claimed = q.claim_next_pending(mode="shadow")
    assert claimed.strategy_id == "first"
    assert claimed.status == PendingOrderStatus.DISPATCHING
    assert claimed.attempts == 1
    assert claimed.dispatched_at is not None


def test_claim_next_pending_respects_mode():
    """claim_next_pending(mode='shadow') doesn't grab a 'live' order."""
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(mode="live"))
    assert q.claim_next_pending(mode="shadow") is None


def test_claim_next_pending_skips_non_pending():
    """Already-dispatching/submitted orders are skipped."""
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    q.update_status(o.id, PendingOrderStatus.DISPATCHING)

    assert q.claim_next_pending(mode="shadow") is None


def test_claim_next_pending_empty_queue_returns_none():
    q = InMemoryPendingOrderQueue()
    assert q.claim_next_pending(mode="shadow") is None


# ================================================================== #
# update_status transitions + last_error + completed_at on terminal
# ================================================================== #
def test_update_status_to_terminal_sets_completed_at():
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    q.update_status(o.id, PendingOrderStatus.FILLED)

    assert o.status == PendingOrderStatus.FILLED
    assert o.completed_at is not None


def test_update_status_non_terminal_no_completed_at():
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    q.update_status(o.id, PendingOrderStatus.SUBMITTED)
    assert o.completed_at is None


def test_update_status_writes_last_error():
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    q.update_status(o.id, PendingOrderStatus.REJECTED, last_error="exchange down")
    assert o.status == PendingOrderStatus.REJECTED
    assert o.last_error == "exchange down"


def test_update_status_increment_attempts():
    q = InMemoryPendingOrderQueue()
    o = make_order(attempts=2)
    q.enqueue(o)
    q.update_status(o.id, PendingOrderStatus.DISPATCHING, increment_attempts=True)
    assert o.attempts == 3


def test_update_status_unknown_id_raises():
    q = InMemoryPendingOrderQueue()
    with pytest.raises(PendingOrderNotFound):
        q.update_status(999, PendingOrderStatus.FILLED)


# ================================================================== #
# list_recent
# ================================================================== #
def test_list_recent_orders_descending_by_created_at():
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(strategy_id="oldest"))
    q.enqueue(make_order(strategy_id="middle"))
    q.enqueue(make_order(strategy_id="newest"))
    rows = q.list_recent()
    assert [r.strategy_id for r in rows] == ["newest", "middle", "oldest"]


def test_list_recent_with_status_filter():
    q = InMemoryPendingOrderQueue()
    a = make_order(strategy_id="a"); q.enqueue(a)
    b = make_order(strategy_id="b"); q.enqueue(b)
    q.update_status(a.id, PendingOrderStatus.FILLED)

    only_filled = q.list_recent(status=PendingOrderStatus.FILLED)
    assert len(only_filled) == 1
    assert only_filled[0].strategy_id == "a"


def test_list_recent_limit():
    q = InMemoryPendingOrderQueue()
    for i in range(10):
        q.enqueue(make_order(strategy_id=f"s{i}"))
    assert len(q.list_recent(limit=3)) == 3


# ================================================================== #
# intent_to_pending
# ================================================================== #
def test_intent_to_pending_long():
    intent = make_intent(direction=Direction.LONG, target=300, sl=0.03)
    order = intent_to_pending(intent, mode="shadow")
    assert order.side == "long"
    assert order.target_notional_usd == 300
    assert order.stop_loss_pct == 0.03
    assert order.mode == "shadow"
    assert order.status == PendingOrderStatus.PENDING


def test_intent_to_pending_short():
    intent = make_intent(direction=Direction.SHORT)
    order = intent_to_pending(intent, mode="paper")
    assert order.side == "short"
    assert order.mode == "paper"


def test_intent_to_pending_neutral_rejected():
    intent = make_intent(direction=Direction.NEUTRAL)
    with pytest.raises(ValueError, match="NEUTRAL"):
        intent_to_pending(intent, mode="shadow")


def test_intent_to_pending_auto_generates_client_order_id():
    intent = make_intent()
    order = intent_to_pending(intent, mode="shadow")
    assert order.client_order_id is not None
    assert order.client_order_id.startswith("sm-test_v1-")
    assert "long" in order.client_order_id


def test_intent_to_pending_explicit_client_order_id_passed_through():
    intent = make_intent()
    order = intent_to_pending(intent, mode="shadow", client_order_id="custom-id-123")
    assert order.client_order_id == "custom-id-123"


def test_intent_to_pending_carries_audit_link():
    intent = make_intent()
    order = intent_to_pending(intent, mode="shadow", fused_signal_id=42)
    assert order.fused_signal_id == 42


# ================================================================== #
# make_intent_callback (composes intent_to_pending + queue.enqueue)
# ================================================================== #
def test_callback_enqueues_and_logs():
    q = InMemoryPendingOrderQueue()
    cb = make_intent_callback(q, mode="shadow")
    cb(make_intent())
    rows = q.list_recent()
    assert len(rows) == 1
    assert rows[0].mode == "shadow"


def test_callback_skips_neutral_silently():
    q = InMemoryPendingOrderQueue()
    cb = make_intent_callback(q, mode="shadow")
    cb(make_intent(direction=Direction.NEUTRAL))   # no exception
    assert q.list_recent() == []


def test_callback_idempotency_via_client_order_id():
    """Two callbacks with intents generating same client_order_id (same
    strategy_id + symbol + side + ts_ms) → only one row."""
    q = InMemoryPendingOrderQueue()
    cb = make_intent_callback(q, mode="shadow")
    intent = make_intent()
    cb(intent)
    cb(intent)   # exactly same intent → idempotent enqueue
    assert len(q.list_recent()) == 1


def test_callback_raises_on_queue_failure():
    """Queue exception propagates so StrategyRuntime can count it."""
    class BrokenQueue:
        def enqueue(self, o):
            raise RuntimeError("DB down")

    cb = make_intent_callback(BrokenQueue(), mode="shadow")
    with pytest.raises(RuntimeError, match="DB down"):
        cb(make_intent())


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""
    q = build_queue(S())
    assert isinstance(q, NoOpPendingOrderQueue)


def test_factory_postgres_when_dsn_set():
    from execution.pending_orders import PostgresPendingOrderQueue
    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""
    q = build_queue(S())
    assert isinstance(q, PostgresPendingOrderQueue)
