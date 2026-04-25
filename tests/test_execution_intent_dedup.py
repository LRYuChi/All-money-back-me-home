"""Tests for IntentDeduper + intent_callback dedup integration (round 44)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.pending_orders import (
    DedupKey,
    InMemoryPendingOrderQueue,
    NoOpIntentDeduper,
    PendingOrder,
    PendingOrderStatus,
    QueueBasedIntentDeduper,
    WindowedIntentDeduper,
    make_intent_callback,
)
from shared.signals.types import Direction, FusedSignal, StrategyIntent


# ================================================================== #
# Helpers
# ================================================================== #
def _make_fused(symbol: str, direction: Direction) -> FusedSignal:
    return FusedSignal(
        symbol=symbol, horizon="1h", direction=direction,
        ensemble_score=0.7, regime="BULL_TRENDING", sources_count=2,
        contributions={"smart_money": 0.5, "ta": 0.2}, conflict=False,
    )


def make_intent(
    strategy: str = "s1",
    symbol: str = "crypto:OKX:BTC/USDT:USDT",
    direction: Direction = Direction.LONG,
    notional: float = 100,
    ts: datetime | None = None,
) -> StrategyIntent:
    return StrategyIntent(
        strategy_id=strategy,
        symbol=symbol,
        direction=direction,
        target_notional_usd=notional,
        entry_price_ref=50_000,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        source_fused=_make_fused(symbol, direction),
        ts=ts or datetime.now(timezone.utc),
    )


# ================================================================== #
# DedupKey
# ================================================================== #
def test_dedup_key_from_long_intent():
    intent = make_intent(direction=Direction.LONG)
    k = DedupKey.from_intent(intent)
    assert k.side == "long"
    assert k.strategy_id == "s1"


def test_dedup_key_from_short_intent():
    intent = make_intent(direction=Direction.SHORT)
    assert DedupKey.from_intent(intent).side == "short"


def test_dedup_key_distinct_strategies_not_equal():
    a = DedupKey.from_intent(make_intent(strategy="s1"))
    b = DedupKey.from_intent(make_intent(strategy="s2"))
    assert a != b


def test_dedup_key_distinct_sides_not_equal():
    a = DedupKey.from_intent(make_intent(direction=Direction.LONG))
    b = DedupKey.from_intent(make_intent(direction=Direction.SHORT))
    assert a != b


# ================================================================== #
# NoOpIntentDeduper
# ================================================================== #
def test_noop_never_duplicates():
    d = NoOpIntentDeduper()
    intent = make_intent()
    d.record(intent)
    d.record(intent)
    assert not d.is_duplicate(intent)


# ================================================================== #
# WindowedIntentDeduper — validation
# ================================================================== #
def test_windowed_rejects_negative_window():
    with pytest.raises(ValueError, match="window_sec"):
        WindowedIntentDeduper(window_sec=-1)


def test_windowed_rejects_zero_max_keys():
    with pytest.raises(ValueError, match="max_keys"):
        WindowedIntentDeduper(window_sec=60, max_keys=0)


# ================================================================== #
# WindowedIntentDeduper — basic dedup
# ================================================================== #
def test_first_intent_is_not_duplicate():
    d = WindowedIntentDeduper(window_sec=60)
    assert not d.is_duplicate(make_intent())


def test_immediate_repeat_is_duplicate():
    d = WindowedIntentDeduper(window_sec=60)
    intent = make_intent()
    d.record(intent)
    # Same intent again right away → duplicate
    assert d.is_duplicate(intent)


def test_zero_window_disables_dedup():
    """window_sec=0 → deduper is a no-op even with explicit record."""
    d = WindowedIntentDeduper(window_sec=0)
    intent = make_intent()
    d.record(intent)
    assert not d.is_duplicate(intent)


def test_intent_outside_window_not_duplicate():
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    d = WindowedIntentDeduper(window_sec=60)
    d.record(make_intent(ts=base_ts))
    # 70s later — outside window
    later = make_intent(ts=base_ts + timedelta(seconds=70))
    assert not d.is_duplicate(later)


def test_intent_at_exact_window_boundary_not_duplicate():
    """≥ window_sec → not duplicate (strict less-than check)."""
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    d = WindowedIntentDeduper(window_sec=60)
    d.record(make_intent(ts=base_ts))
    boundary = make_intent(ts=base_ts + timedelta(seconds=60))
    assert not d.is_duplicate(boundary)


def test_intent_just_inside_window_is_duplicate():
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    d = WindowedIntentDeduper(window_sec=60)
    d.record(make_intent(ts=base_ts))
    just_inside = make_intent(ts=base_ts + timedelta(seconds=59))
    assert d.is_duplicate(just_inside)


# ================================================================== #
# WindowedIntentDeduper — distinct keys not deduped
# ================================================================== #
def test_different_strategies_not_deduped():
    d = WindowedIntentDeduper(window_sec=300)
    d.record(make_intent(strategy="s1"))
    other = make_intent(strategy="s2")
    assert not d.is_duplicate(other)


def test_different_symbols_not_deduped():
    d = WindowedIntentDeduper(window_sec=300)
    d.record(make_intent(symbol="crypto:OKX:BTC/USDT:USDT"))
    other = make_intent(symbol="crypto:OKX:ETH/USDT:USDT")
    assert not d.is_duplicate(other)


def test_different_sides_not_deduped():
    """Long then short of same symbol = legit reverse, NOT a duplicate."""
    d = WindowedIntentDeduper(window_sec=300)
    d.record(make_intent(direction=Direction.LONG))
    other = make_intent(direction=Direction.SHORT)
    assert not d.is_duplicate(other)


# ================================================================== #
# WindowedIntentDeduper — naive timestamp tolerance
# ================================================================== #
def test_naive_timestamp_treated_as_utc():
    """Defensive: caller may pass naive ts; coerce to UTC."""
    d = WindowedIntentDeduper(window_sec=60)
    naive_ts = datetime(2026, 4, 25, 12, 0)   # no tzinfo
    intent = make_intent(ts=naive_ts)
    d.record(intent)
    assert d.is_duplicate(intent)


# ================================================================== #
# WindowedIntentDeduper — eviction (memory bound)
# ================================================================== #
def test_eviction_keeps_size_under_max_keys():
    d = WindowedIntentDeduper(window_sec=300, max_keys=10)
    base = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    for i in range(50):
        d.record(make_intent(strategy=f"s{i}", ts=base + timedelta(seconds=i)))
    # After 50 inserts with cap=10, size should be ≤ 10
    assert d.size() <= 10


def test_eviction_drops_oldest_first():
    d = WindowedIntentDeduper(window_sec=300, max_keys=2)
    base = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    # Record 3 distinct keys at increasing ts
    d.record(make_intent(strategy="oldest", ts=base))
    d.record(make_intent(strategy="middle", ts=base + timedelta(seconds=10)))
    d.record(make_intent(strategy="newest", ts=base + timedelta(seconds=20)))
    # `oldest` should have been evicted (LRU); is_duplicate returns False
    assert not d.is_duplicate(make_intent(
        strategy="oldest", ts=base + timedelta(seconds=21),
    ))
    # `newest` still in cache
    assert d.is_duplicate(make_intent(
        strategy="newest", ts=base + timedelta(seconds=21),
    ))


def test_size_introspection():
    d = WindowedIntentDeduper(window_sec=60)
    assert d.size() == 0
    d.record(make_intent(strategy="s1"))
    d.record(make_intent(strategy="s2"))
    assert d.size() == 2


# ================================================================== #
# QueueBasedIntentDeduper
# ================================================================== #
def test_queue_based_first_intent_not_duplicate():
    q = InMemoryPendingOrderQueue()
    d = QueueBasedIntentDeduper(q, window_sec=60)
    assert not d.is_duplicate(make_intent())


def test_queue_based_finds_recent_matching_order():
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    q = InMemoryPendingOrderQueue()
    # Inject a row that matches strategy/symbol/side and is fresh
    o = PendingOrder(
        strategy_id="s1", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="shadow",
        created_at=base_ts,
    )
    q.enqueue(o)

    d = QueueBasedIntentDeduper(q, window_sec=60)
    new_intent = make_intent(ts=base_ts + timedelta(seconds=30))
    assert d.is_duplicate(new_intent)


def test_queue_based_skips_old_orders():
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    q = InMemoryPendingOrderQueue()
    old = PendingOrder(
        strategy_id="s1", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="shadow",
        created_at=base_ts,
    )
    q.enqueue(old)

    d = QueueBasedIntentDeduper(q, window_sec=60)
    fresh_intent = make_intent(ts=base_ts + timedelta(seconds=120))
    assert not d.is_duplicate(fresh_intent)


def test_queue_based_skips_different_side():
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    q = InMemoryPendingOrderQueue()
    short_o = PendingOrder(
        strategy_id="s1", symbol="crypto:OKX:BTC/USDT:USDT", side="short",
        target_notional_usd=100, mode="shadow",
        created_at=base_ts,
    )
    q.enqueue(short_o)

    d = QueueBasedIntentDeduper(q, window_sec=300)
    long_intent = make_intent(direction=Direction.LONG,
                              ts=base_ts + timedelta(seconds=10))
    assert not d.is_duplicate(long_intent)


def test_queue_based_failure_fails_open():
    """list_recent crashes → don't block trading; treat as not-duplicate."""
    class BoomQueue:
        def list_recent(self, **_):
            raise ConnectionError("DB down")
    d = QueueBasedIntentDeduper(BoomQueue(), window_sec=60)
    assert not d.is_duplicate(make_intent())


def test_queue_based_record_is_noop():
    """record() doesn't add anything — queue.enqueue is the source of truth."""
    q = InMemoryPendingOrderQueue()
    d = QueueBasedIntentDeduper(q, window_sec=60)
    intent = make_intent()
    d.record(intent)   # must not raise
    # Queue should NOT have any new rows (record is no-op)
    assert q.list_recent() == []


# ================================================================== #
# make_intent_callback — dedup integration
# ================================================================== #
def test_callback_without_deduper_enqueues_every_intent():
    """Backwards compat: no deduper kwarg → all intents enqueue."""
    q = InMemoryPendingOrderQueue()
    cb = make_intent_callback(q, mode="shadow")
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    cb(make_intent(ts=base_ts))
    cb(make_intent(ts=base_ts + timedelta(seconds=1)))
    assert len(q.list_recent()) == 2


def test_callback_with_deduper_skips_duplicates():
    q = InMemoryPendingOrderQueue()
    deduper = WindowedIntentDeduper(window_sec=60)
    cb = make_intent_callback(q, mode="shadow", deduper=deduper)
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    cb(make_intent(ts=base_ts))
    cb(make_intent(ts=base_ts + timedelta(seconds=10)))   # within 60s

    assert len(q.list_recent()) == 1


def test_callback_dedup_logs_skip(caplog):
    import logging
    q = InMemoryPendingOrderQueue()
    deduper = WindowedIntentDeduper(window_sec=60)
    cb = make_intent_callback(q, mode="shadow", deduper=deduper)
    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)

    cb(make_intent(ts=base_ts))
    with caplog.at_level(logging.WARNING):
        cb(make_intent(ts=base_ts + timedelta(seconds=10)))
    assert any("SKIP duplicate intent" in m for m in caplog.messages)


def test_callback_records_only_after_successful_enqueue():
    """If enqueue fails, deduper should not record — caller may retry."""
    deduper = WindowedIntentDeduper(window_sec=60)

    class BoomQueue:
        def enqueue(self, order):
            raise ConnectionError("DB down on first try")

    cb = make_intent_callback(BoomQueue(), mode="shadow", deduper=deduper)
    intent = make_intent()
    with pytest.raises(ConnectionError):
        cb(intent)
    # Deduper must NOT have recorded the failed intent
    assert deduper.size() == 0


def test_callback_deduper_failure_fails_open():
    """A buggy deduper.is_duplicate must NOT block enqueue."""
    q = InMemoryPendingOrderQueue()

    class BadDeduper:
        def is_duplicate(self, intent):
            raise RuntimeError("deduper broken")
        def record(self, intent):
            return

    cb = make_intent_callback(q, mode="shadow", deduper=BadDeduper())
    cb(make_intent())
    # Order STILL enqueued despite deduper exception
    assert len(q.list_recent()) == 1


def test_callback_deduper_record_failure_does_not_block():
    """deduper.record() crashing post-enqueue is logged + ignored."""
    q = InMemoryPendingOrderQueue()

    class HalfDeduper:
        def is_duplicate(self, intent): return False
        def record(self, intent): raise RuntimeError("boom")

    cb = make_intent_callback(q, mode="shadow", deduper=HalfDeduper())
    cb(make_intent())   # must not raise
    assert len(q.list_recent()) == 1


def test_callback_neutral_intent_skipped_before_dedup():
    """NEUTRAL intent is filtered before dedup runs (no record consumed)."""
    deduper = WindowedIntentDeduper(window_sec=60)
    q = InMemoryPendingOrderQueue()
    cb = make_intent_callback(q, mode="shadow", deduper=deduper)
    cb(make_intent(direction=Direction.NEUTRAL))
    assert deduper.size() == 0
    assert len(q.list_recent()) == 0


# ================================================================== #
# E2E: prevents the actual double-position scenario
# ================================================================== #
def test_e2e_two_quick_intents_only_open_one_position():
    """Realistic: strategy fires twice in quick succession (e.g. signal
    aggregator double-counts a wallet event). Without dedup → 2 orders →
    2 fills. With dedup → 1 order."""
    q = InMemoryPendingOrderQueue()
    deduper = WindowedIntentDeduper(window_sec=120)
    cb = make_intent_callback(q, mode="paper", deduper=deduper)

    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    cb(make_intent(ts=base_ts))
    cb(make_intent(ts=base_ts + timedelta(milliseconds=100)))   # 0.1s later
    cb(make_intent(ts=base_ts + timedelta(seconds=30)))          # 30s later

    # All three within 120s → only the first opens an order
    orders = q.list_recent()
    assert len(orders) == 1
    assert orders[0].status == PendingOrderStatus.PENDING


def test_e2e_intents_after_window_re_enable_trading():
    """After the window passes, the same (strategy, symbol, side) can
    open a NEW position (e.g. closing previous + reopening)."""
    q = InMemoryPendingOrderQueue()
    deduper = WindowedIntentDeduper(window_sec=60)
    cb = make_intent_callback(q, mode="paper", deduper=deduper)

    base_ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    cb(make_intent(ts=base_ts))
    cb(make_intent(ts=base_ts + timedelta(seconds=120)))   # outside window
    assert len(q.list_recent()) == 2
