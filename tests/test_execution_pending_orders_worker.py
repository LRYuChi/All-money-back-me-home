"""Tests for execution.pending_orders.worker — Worker + Dispatchers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from execution.pending_orders import (
    DispatchResult,
    InMemoryPendingOrderQueue,
    LogOnlyDispatcher,
    PendingOrder,
    PendingOrderStatus,
    PendingOrderWorker,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(strategy_id="x", mode="shadow") -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy_id,
        symbol="X",
        side="long",
        target_notional_usd=100.0,
        mode=mode,
    )


# ================================================================== #
# LogOnlyDispatcher
# ================================================================== #
def test_log_only_dispatcher_returns_filled():
    d = LogOnlyDispatcher(mode="shadow")
    assert d.mode == "shadow"
    result = d.dispatch(make_order())
    assert isinstance(result, DispatchResult)
    assert result.terminal_status == PendingOrderStatus.FILLED


def test_log_only_dispatcher_carries_detail():
    d = LogOnlyDispatcher(mode="notify")
    result = d.dispatch(make_order())
    assert result.detail["dispatcher"] == "log_only"
    assert result.detail["mode"] == "notify"


# ================================================================== #
# Worker.process_one — happy + empty
# ================================================================== #
def test_process_one_empty_queue_returns_zero():
    q = InMemoryPendingOrderQueue()
    w = PendingOrderWorker(q, LogOnlyDispatcher())
    assert w.process_one() == 0


def test_process_one_claims_and_marks_filled():
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)

    w = PendingOrderWorker(q, LogOnlyDispatcher())
    assert w.process_one() == 1

    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.FILLED
    assert refreshed.completed_at is not None
    assert w.stats()["claimed"] == 1
    assert w.stats()["filled"] == 1


def test_process_one_only_consumes_matching_mode():
    """Worker for shadow mode shouldn't claim a `live` order."""
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(mode="live"))

    w = PendingOrderWorker(q, LogOnlyDispatcher(mode="shadow"))
    assert w.process_one() == 0

    # Still pending
    rows = q.list_recent()
    assert rows[0].status == PendingOrderStatus.PENDING


def test_process_one_processes_in_fifo_order():
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(strategy_id="first"))
    q.enqueue(make_order(strategy_id="second"))

    w = PendingOrderWorker(q, LogOnlyDispatcher())
    w.process_one()
    w.process_one()

    rows = sorted(q.list_recent(), key=lambda o: o.id)
    assert rows[0].strategy_id == "first"
    assert rows[0].status == PendingOrderStatus.FILLED
    assert rows[1].strategy_id == "second"
    assert rows[1].status == PendingOrderStatus.FILLED


# ================================================================== #
# Worker handles dispatcher failures (REJECTED + last_error)
# ================================================================== #
class FailingDispatcher:
    @property
    def mode(self):
        return "shadow"

    def dispatch(self, order):
        raise RuntimeError("OKX 500")


def test_dispatcher_exception_marks_rejected_with_error():
    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)

    w = PendingOrderWorker(q, FailingDispatcher())
    w.process_one()

    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert "OKX 500" in (refreshed.last_error or "")
    assert w.stats()["dispatcher_errors"] == 1
    assert w.stats()["rejected"] == 1


def test_dispatcher_returning_partial_fill_does_not_count_terminal():
    """PARTIALLY_FILLED leaves the order open."""
    class PartialDispatcher:
        @property
        def mode(self):
            return "shadow"

        def dispatch(self, order):
            return DispatchResult(
                terminal_status=PendingOrderStatus.PARTIALLY_FILLED,
            )

    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    w = PendingOrderWorker(q, PartialDispatcher())
    w.process_one()

    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.PARTIALLY_FILLED
    assert refreshed.is_terminal is False
    assert refreshed.completed_at is None  # not terminal → no completed_at
    assert w.stats()["partially_filled"] == 1


def test_dispatcher_explicit_rejected_writes_error():
    class RejectingDispatcher:
        @property
        def mode(self):
            return "shadow"

        def dispatch(self, order):
            return DispatchResult(
                terminal_status=PendingOrderStatus.REJECTED,
                last_error="exchange said no",
            )

    q = InMemoryPendingOrderQueue()
    o = make_order()
    q.enqueue(o)
    w = PendingOrderWorker(q, RejectingDispatcher())
    w.process_one()

    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert refreshed.last_error == "exchange said no"


# ================================================================== #
# Stats counters
# ================================================================== #
def test_stats_counters_update_per_outcome():
    q = InMemoryPendingOrderQueue()
    for _ in range(3):
        q.enqueue(make_order())

    w = PendingOrderWorker(q, LogOnlyDispatcher())
    for _ in range(3):
        w.process_one()

    s = w.stats()
    assert s["claimed"] == 3
    assert s["filled"] == 3


def test_reset_stats():
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order())
    w = PendingOrderWorker(q, LogOnlyDispatcher())
    w.process_one()
    w.reset_stats()
    assert w.stats()["claimed"] == 0


# ================================================================== #
# Async run_forever — graceful shutdown
# ================================================================== #
@pytest.mark.asyncio
async def test_run_forever_stops_on_event():
    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order())
    w = PendingOrderWorker(q, LogOnlyDispatcher(), idle_sleep_sec=0.05)

    stop = asyncio.Event()

    # Schedule a stop after 100ms — gives worker time to drain the one order
    async def _stop_soon():
        await asyncio.sleep(0.1)
        stop.set()

    task = asyncio.create_task(_stop_soon())
    await asyncio.wait_for(w.run_forever(stop), timeout=1.0)
    await task

    # Order should be filled
    assert w.stats()["claimed"] == 1
    assert w.stats()["filled"] == 1


@pytest.mark.asyncio
async def test_run_forever_handles_burst_then_idle():
    """Worker processes 5 orders in a burst then idles correctly."""
    q = InMemoryPendingOrderQueue()
    for _ in range(5):
        q.enqueue(make_order())

    w = PendingOrderWorker(q, LogOnlyDispatcher(), idle_sleep_sec=0.05)
    stop = asyncio.Event()

    async def _stop_after_drain():
        # Wait until queue idle for 100ms then stop
        for _ in range(20):
            await asyncio.sleep(0.05)
            if w.stats()["filled"] >= 5:
                # Give worker one more poll cycle to confirm idle
                await asyncio.sleep(0.1)
                stop.set()
                return

    task = asyncio.create_task(_stop_after_drain())
    await asyncio.wait_for(w.run_forever(stop), timeout=2.0)
    await task

    assert w.stats()["filled"] == 5


@pytest.mark.asyncio
async def test_run_forever_continues_after_dispatcher_error():
    """One failing order shouldn't stop subsequent orders from processing."""
    class IntermittentDispatcher:
        def __init__(self):
            self.calls = 0

        @property
        def mode(self):
            return "shadow"

        def dispatch(self, order):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first call fails")
            return DispatchResult(terminal_status=PendingOrderStatus.FILLED)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(strategy_id="will_fail"))
    q.enqueue(make_order(strategy_id="will_succeed"))

    d = IntermittentDispatcher()
    w = PendingOrderWorker(q, d, idle_sleep_sec=0.05)
    stop = asyncio.Event()

    async def _stop_soon():
        await asyncio.sleep(0.3)
        stop.set()

    task = asyncio.create_task(_stop_soon())
    await asyncio.wait_for(w.run_forever(stop), timeout=2.0)
    await task

    assert w.stats()["claimed"] == 2
    assert w.stats()["rejected"] == 1
    assert w.stats()["filled"] == 1
