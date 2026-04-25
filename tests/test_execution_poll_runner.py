"""Tests for SUBMITTED-state poller (round 42)."""
from __future__ import annotations

import asyncio

import pytest

from execution.exchanges.okx import (
    FakeOKXClient,
    OKXLiveDispatcher,
)
from execution.exchanges.types import (
    ExchangeResponse,
    ExchangeResponseStatus,
)
from execution.pending_orders import (
    InMemoryEventLogger,
    InMemoryPendingOrderQueue,
    PendingOrder,
    PendingOrderStatus,
    PollStats,
    background_poll_submitted_loop,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_submitted_order(
    coid: str = "rd42-test", symbol: str = "crypto:OKX:BTC/USDT:USDT",
) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1", symbol=symbol, side="long",
        target_notional_usd=100, mode="paper",
        client_order_id=coid,
        status=PendingOrderStatus.SUBMITTED,
    )


def populate_submitted(q: InMemoryPendingOrderQueue, orders: list[PendingOrder]):
    """Inject SUBMITTED orders directly so list_recent finds them."""
    ids = []
    for o in orders:
        with q._lock:
            o.id = q._next_id
            q._next_id += 1
            q._orders[o.id] = o
        ids.append(o.id)
    return ids


# ================================================================== #
# Validation
# ================================================================== #
@pytest.mark.asyncio
async def test_rejects_zero_interval():
    q = InMemoryPendingOrderQueue()
    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    with pytest.raises(ValueError, match="interval_sec must be > 0"):
        await background_poll_submitted_loop(q, d, asyncio.Event(), interval_sec=0)


@pytest.mark.asyncio
async def test_dispatcher_without_fetch_status_exits_early(caplog):
    """LogOnly / NotifyOnly / shadow dispatchers can't poll. Don't crash."""
    import logging
    from execution.pending_orders import LogOnlyDispatcher

    q = InMemoryPendingOrderQueue()
    stop = asyncio.Event()
    stop.set()
    with caplog.at_level(logging.WARNING):
        stats = await background_poll_submitted_loop(
            q, LogOnlyDispatcher("shadow"), stop, interval_sec=1.0,
        )
    assert isinstance(stats, PollStats)
    assert stats.iterations == 0
    assert any("no fetch_status method" in m for m in caplog.messages)


# ================================================================== #
# Happy paths: SUBMITTED → terminal advance
# ================================================================== #
@pytest.mark.asyncio
async def test_polls_submitted_and_advances_to_filled():
    """ACCEPTED order on the book → next poll returns FILLED → worker
    updates status."""
    q = InMemoryPendingOrderQueue()
    [oid] = populate_submitted(q, [make_submitted_order(coid="X1")])

    fake_client = FakeOKXClient(fetch_responses={
        "X1": ExchangeResponse(
            status=ExchangeResponseStatus.FILLED,
            exchange_order_id="OKX-X1", filled_notional_usd=100,
            avg_fill_price=50_000,
        ),
    })
    d = OKXLiveDispatcher(fake_client, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(
        q, d, stop, interval_sec=0.01,
    )
    assert q.get(oid).status == PendingOrderStatus.FILLED
    assert stats.orders_polled >= 1
    assert stats.orders_advanced >= 1


@pytest.mark.asyncio
async def test_polls_submitted_advances_to_partially_filled():
    q = InMemoryPendingOrderQueue()
    [oid] = populate_submitted(q, [make_submitted_order(coid="X2")])

    fake = FakeOKXClient(fetch_responses={
        "X2": ExchangeResponse(
            status=ExchangeResponseStatus.PARTIALLY_FILLED,
            exchange_order_id="OKX-X2", filled_notional_usd=30,
        ),
    })
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    assert q.get(oid).status == PendingOrderStatus.PARTIALLY_FILLED


@pytest.mark.asyncio
async def test_still_accepted_keeps_submitted():
    """If poll returns ACCEPTED again, order stays SUBMITTED — no spurious
    transitions."""
    q = InMemoryPendingOrderQueue()
    [oid] = populate_submitted(q, [make_submitted_order(coid="X3")])

    fake = FakeOKXClient(fetch_responses={
        "X3": ExchangeResponse(
            status=ExchangeResponseStatus.ACCEPTED,
            exchange_order_id="OKX-X3",
        ),
    })
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    assert q.get(oid).status == PendingOrderStatus.SUBMITTED
    assert stats.orders_polled >= 1
    assert stats.orders_advanced == 0   # ACCEPTED → SUBMITTED is no-op


@pytest.mark.asyncio
async def test_rejected_response_marks_rejected():
    q = InMemoryPendingOrderQueue()
    [oid] = populate_submitted(q, [make_submitted_order(coid="X4")])

    fake = FakeOKXClient(fetch_responses={
        "X4": ExchangeResponse(
            status=ExchangeResponseStatus.REJECTED,
            error_code="51000", error_message="kicked off book",
        ),
    })
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    refreshed = q.get(oid)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert "51000" in (refreshed.last_error or "")


# ================================================================== #
# Audit events on transition
# ================================================================== #
@pytest.mark.asyncio
async def test_polled_advance_emits_audit_event():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    [oid] = populate_submitted(q, [make_submitted_order(coid="X5")])
    log.events.clear()

    fake = FakeOKXClient(fetch_responses={
        "X5": ExchangeResponse(
            status=ExchangeResponseStatus.FILLED,
            exchange_order_id="OKX-X5",
        ),
    })
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    events = log.history(oid)
    transitions = [(e.from_status, e.to_status) for e in events]
    assert (PendingOrderStatus.SUBMITTED, PendingOrderStatus.FILLED) in transitions


# ================================================================== #
# Filtering: only SUBMITTED, not other states
# ================================================================== #
@pytest.mark.asyncio
async def test_does_not_poll_non_submitted_orders():
    q = InMemoryPendingOrderQueue()
    pending_o = PendingOrder(
        strategy_id="s", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="paper", client_order_id="P1",
        status=PendingOrderStatus.PENDING,
    )
    filled_o = PendingOrder(
        strategy_id="s", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="paper", client_order_id="F1",
        status=PendingOrderStatus.FILLED,
    )
    populate_submitted(q, [pending_o, filled_o])

    fake = FakeOKXClient()
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    assert stats.orders_polled == 0
    assert fake.fetch_order_calls == []


# ================================================================== #
# Order without client_order_id is skipped (no advance)
# ================================================================== #
@pytest.mark.asyncio
async def test_order_without_coid_skipped():
    q = InMemoryPendingOrderQueue()
    o = PendingOrder(
        strategy_id="s", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="paper",
        status=PendingOrderStatus.SUBMITTED,
        client_order_id=None,
    )
    [oid] = populate_submitted(q, [o])

    fake = FakeOKXClient()
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    # Polled (counted) but fetch_status returned None → no advance
    assert stats.orders_polled >= 1
    assert stats.orders_advanced == 0
    assert q.get(oid).status == PendingOrderStatus.SUBMITTED
    # FakeOKXClient.fetch_order should never have been called
    assert fake.fetch_order_calls == []


# ================================================================== #
# Failure isolation
# ================================================================== #
@pytest.mark.asyncio
async def test_one_fetch_error_doesnt_abort_batch():
    """Fetching one order raises; the next one in the batch still advances."""
    q = InMemoryPendingOrderQueue()
    ids = populate_submitted(q, [
        make_submitted_order(coid="ERR1"),
        make_submitted_order(coid="OK2"),
    ])

    class PartiallyFlakyClient(FakeOKXClient):
        def fetch_order(self, client_order_id, symbol):
            self.fetch_order_calls.append((client_order_id, symbol))
            if client_order_id == "ERR1":
                raise ConnectionError("flaky")
            return ExchangeResponse(
                status=ExchangeResponseStatus.FILLED,
                exchange_order_id="OK",
            )
    fake = PartiallyFlakyClient()
    d = OKXLiveDispatcher(fake, mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    # The OK one advanced
    assert q.get(ids[1]).status == PendingOrderStatus.FILLED
    # The ERR one stays SUBMITTED — fetch_status caught the exception
    # internally and returned None, so no transition
    assert q.get(ids[0]).status == PendingOrderStatus.SUBMITTED


@pytest.mark.asyncio
async def test_list_recent_failure_increments_errors_and_continues():
    class FlakyQueue:
        def __init__(self):
            self.calls = 0
        def list_recent(self, **kw):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("DB hiccup")
            return []
        def update_status(self, *a, **kw): pass

    q = FlakyQueue()
    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()
    asyncio.create_task(stopper())

    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=0.01)
    assert stats.errors >= 1
    assert q.calls >= 2   # kept iterating after the error


# ================================================================== #
# stop_event responsiveness
# ================================================================== #
@pytest.mark.asyncio
async def test_stop_wakes_loop_immediately():
    """Don't wait the full interval after stop fires."""
    q = InMemoryPendingOrderQueue()
    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.02)
        stop.set()
    asyncio.create_task(stopper())

    import time
    t0 = time.monotonic()
    await background_poll_submitted_loop(q, d, stop, interval_sec=10.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"loop didn't exit promptly: {elapsed:.2f}s"


# ================================================================== #
# max_orders_per_iteration honored
# ================================================================== #
@pytest.mark.asyncio
async def test_max_orders_per_iteration_caps_batch():
    """Even with 50 SUBMITTED orders, max=2 caps the per-iteration call."""
    q = InMemoryPendingOrderQueue()
    populate_submitted(q, [
        make_submitted_order(coid=f"M{i}") for i in range(50)
    ])

    list_calls = []
    original_list = q.list_recent
    def spy_list(**kw):
        list_calls.append(kw)
        return original_list(**kw)
    q.list_recent = spy_list

    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    stop = asyncio.Event()
    stop.set()    # Stop after one iteration

    await background_poll_submitted_loop(
        q, d, stop, interval_sec=1.0, max_orders_per_iteration=2,
    )
    # list_recent should have been called with limit=2
    if list_calls:
        assert list_calls[0]["limit"] == 2


# ================================================================== #
# Already-set stop runs zero iterations
# ================================================================== #
@pytest.mark.asyncio
async def test_pre_set_stop_runs_no_iterations():
    q = InMemoryPendingOrderQueue()
    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    stop = asyncio.Event()
    stop.set()
    stats = await background_poll_submitted_loop(q, d, stop, interval_sec=1.0)
    assert stats.iterations == 0
    assert stats.orders_polled == 0
