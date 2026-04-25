"""Tests for background_sweep_loop helper (round 38)."""
from __future__ import annotations

import asyncio

import pytest

from execution.pending_orders import (
    InMemoryPendingOrderQueue,
    SweepStats,
    background_sweep_loop,
)


# ================================================================== #
# Validation
# ================================================================== #
@pytest.mark.asyncio
async def test_rejects_zero_interval():
    q = InMemoryPendingOrderQueue()
    with pytest.raises(ValueError, match="interval_sec must be > 0"):
        await background_sweep_loop(
            q, asyncio.Event(), interval_sec=0, pending_max_age_sec=60,
        )


@pytest.mark.asyncio
async def test_rejects_negative_interval():
    q = InMemoryPendingOrderQueue()
    with pytest.raises(ValueError):
        await background_sweep_loop(
            q, asyncio.Event(), interval_sec=-1, pending_max_age_sec=60,
        )


@pytest.mark.asyncio
async def test_warns_when_both_thresholds_zero(caplog):
    """The loop runs but never expires anything — log a warning."""
    import logging
    q = InMemoryPendingOrderQueue()
    stop = asyncio.Event()
    stop.set()    # stop immediately so we don't hang
    with caplog.at_level(logging.WARNING):
        await background_sweep_loop(
            q, stop, interval_sec=1.0,
            pending_max_age_sec=0, dispatching_max_age_sec=0,
        )
    assert any("never expire anything" in m for m in caplog.messages)


# ================================================================== #
# Args plumbed through
# ================================================================== #
@pytest.mark.asyncio
async def test_args_passed_to_sweep_expired():
    captured: list[dict] = []

    class CapturingQueue:
        def sweep_expired(self, *, pending_max_age_sec, dispatching_max_age_sec):
            captured.append({
                "pending": pending_max_age_sec,
                "dispatching": dispatching_max_age_sec,
            })
            return 0

    stop = asyncio.Event()

    async def stop_after_one():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.create_task(stop_after_one())
    await background_sweep_loop(
        CapturingQueue(), stop,
        interval_sec=0.01,
        pending_max_age_sec=300,
        dispatching_max_age_sec=60,
    )
    assert captured
    assert captured[0]["pending"] == 300
    assert captured[0]["dispatching"] == 60


# ================================================================== #
# Iteration cadence
# ================================================================== #
@pytest.mark.asyncio
async def test_loop_iterates_multiple_times():
    """At interval=0.02, ~5 iterations should fit in a 0.1s window."""
    counter = {"n": 0}

    class CountingQueue:
        def sweep_expired(self, **_):
            counter["n"] += 1
            return 0

    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.1)
        stop.set()

    asyncio.create_task(stopper())
    stats = await background_sweep_loop(
        CountingQueue(), stop,
        interval_sec=0.02,
        pending_max_age_sec=60,
    )
    # Expect 3-7 iterations depending on event-loop precision
    assert counter["n"] >= 2
    assert stats.iterations == counter["n"]


# ================================================================== #
# stop_event responsiveness
# ================================================================== #
@pytest.mark.asyncio
async def test_stop_event_wakes_loop_immediately():
    """Don't wait the full interval after stop is set — exit promptly."""
    counter = {"n": 0}

    class CountingQueue:
        def sweep_expired(self, **_):
            counter["n"] += 1
            return 0

    stop = asyncio.Event()

    # Set stop almost immediately; total run time should be << 10s
    async def stopper():
        await asyncio.sleep(0.02)
        stop.set()

    asyncio.create_task(stopper())
    import time
    t0 = time.monotonic()
    await background_sweep_loop(
        CountingQueue(), stop,
        interval_sec=10.0,            # would normally block 10s
        pending_max_age_sec=60,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"loop didn't exit promptly after stop: {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_stop_already_set_runs_zero_iterations():
    """If stop is already set when called, sweeper should exit immediately."""
    counter = {"n": 0}

    class CountingQueue:
        def sweep_expired(self, **_):
            counter["n"] += 1
            return 0

    stop = asyncio.Event()
    stop.set()
    stats = await background_sweep_loop(
        CountingQueue(), stop, interval_sec=1.0, pending_max_age_sec=60,
    )
    assert counter["n"] == 0
    assert stats.iterations == 0


# ================================================================== #
# Failure isolation
# ================================================================== #
@pytest.mark.asyncio
async def test_sweep_exception_increments_errors_and_continues():
    """One flaky tick must not kill the sidecar."""
    n_calls = {"n": 0}

    class FlakyQueue:
        def sweep_expired(self, **_):
            n_calls["n"] += 1
            if n_calls["n"] == 1:
                raise ConnectionError("DB hiccup")
            return 0

    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.create_task(stopper())
    stats = await background_sweep_loop(
        FlakyQueue(), stop,
        interval_sec=0.01, pending_max_age_sec=60,
    )
    assert stats.errors >= 1
    assert n_calls["n"] >= 2   # kept going after the error


# ================================================================== #
# Stats accumulate expired counts
# ================================================================== #
@pytest.mark.asyncio
async def test_total_expired_accumulates_across_iterations():
    expired_per_call = [3, 0, 5, 2]
    idx = {"i": 0}

    class StaticQueue:
        def sweep_expired(self, **_):
            i = idx["i"]
            idx["i"] += 1
            return expired_per_call[i] if i < len(expired_per_call) else 0

    stop = asyncio.Event()

    async def stopper():
        # Wait for at least 4 iterations
        await asyncio.sleep(0.1)
        stop.set()

    asyncio.create_task(stopper())
    stats = await background_sweep_loop(
        StaticQueue(), stop,
        interval_sec=0.01, pending_max_age_sec=60,
    )
    # First 4 iterations expired 3+0+5+2 = 10; later iterations may add 0
    assert stats.total_expired >= 10


# ================================================================== #
# Returns SweepStats type
# ================================================================== #
@pytest.mark.asyncio
async def test_returns_sweep_stats_dataclass():
    q = InMemoryPendingOrderQueue()
    stop = asyncio.Event()
    stop.set()
    stats = await background_sweep_loop(
        q, stop, interval_sec=1.0, pending_max_age_sec=60,
    )
    assert isinstance(stats, SweepStats)
    assert stats.iterations == 0
    assert stats.total_expired == 0
    assert stats.errors == 0


# ================================================================== #
# End-to-end: sweeper actually expires real orders
# ================================================================== #
@pytest.mark.asyncio
async def test_e2e_sweeper_expires_old_pending_order():
    from datetime import datetime, timedelta, timezone

    from execution.pending_orders import PendingOrder, PendingOrderStatus

    q = InMemoryPendingOrderQueue()
    # Inject an old PENDING directly
    old = PendingOrder(
        strategy_id="s1", symbol="X", side="long",
        target_notional_usd=100, mode="shadow",
        created_at=datetime.now(timezone.utc) - timedelta(seconds=300),
    )
    with q._lock:
        old.id = q._next_id
        q._next_id += 1
        q._orders[old.id] = old

    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    asyncio.create_task(stopper())
    stats = await background_sweep_loop(
        q, stop,
        interval_sec=0.01, pending_max_age_sec=60,
    )
    assert q.get(old.id).status == PendingOrderStatus.EXPIRED
    assert stats.total_expired >= 1
