"""Tests for sweep_expired across queue backends + CLI (round 37)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.pending_orders import (
    InMemoryEventLogger,
    InMemoryPendingOrderQueue,
    NoOpPendingOrderQueue,
    PendingOrder,
    PendingOrderStatus,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    status=PendingOrderStatus.PENDING,
    age_sec: float = 0,
    dispatched_age_sec: float | None = None,
    mode: str = "shadow",
) -> PendingOrder:
    """Create an order with synthetic timestamps so sweep can age it."""
    now = datetime.now(timezone.utc)
    o = PendingOrder(
        strategy_id="s1", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=500.0, mode=mode,
        status=status,
        created_at=now - timedelta(seconds=age_sec),
        updated_at=now - timedelta(seconds=age_sec),
    )
    if dispatched_age_sec is not None:
        o.dispatched_at = now - timedelta(seconds=dispatched_age_sec)
    return o


def populate(q: InMemoryPendingOrderQueue, orders: list[PendingOrder]) -> list[int]:
    """Inject orders directly so we control timestamps (enqueue would
    overwrite created_at). Returns assigned ids."""
    ids: list[int] = []
    for o in orders:
        with q._lock:
            o.id = q._next_id
            q._next_id += 1
            q._orders[o.id] = o
        ids.append(o.id)
    return ids


# ================================================================== #
# NoOp backend
# ================================================================== #
def test_noop_sweep_returns_zero():
    q = NoOpPendingOrderQueue()
    assert q.sweep_expired(pending_max_age_sec=60) == 0


# ================================================================== #
# Default behavior — both thresholds 0 = no-op
# ================================================================== #
def test_sweep_with_both_thresholds_zero_is_noop():
    q = InMemoryPendingOrderQueue()
    populate(q, [make_order(age_sec=999)])
    n = q.sweep_expired(pending_max_age_sec=0, dispatching_max_age_sec=0)
    assert n == 0


# ================================================================== #
# PENDING expiry
# ================================================================== #
def test_sweep_expires_old_pending():
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [make_order(age_sec=120)])
    n = q.sweep_expired(pending_max_age_sec=60)
    assert n == 1
    assert q.get(oid).status == PendingOrderStatus.EXPIRED


def test_sweep_keeps_young_pending():
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [make_order(age_sec=10)])
    assert q.sweep_expired(pending_max_age_sec=60) == 0
    assert q.get(oid).status == PendingOrderStatus.PENDING


def test_sweep_threshold_is_inclusive():
    """age >= threshold expires (boundary semantic worth pinning)."""
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [make_order(age_sec=60.5)])
    assert q.sweep_expired(pending_max_age_sec=60) == 1
    assert q.get(oid).status == PendingOrderStatus.EXPIRED


def test_sweep_skips_pending_when_threshold_zero():
    q = InMemoryPendingOrderQueue()
    populate(q, [make_order(age_sec=999)])
    n = q.sweep_expired(pending_max_age_sec=0, dispatching_max_age_sec=999)
    assert n == 0


# ================================================================== #
# DISPATCHING expiry
# ================================================================== #
def test_sweep_expires_old_dispatching():
    """Worker crashed mid-dispatch — orders stuck in DISPATCHING forever."""
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [
        make_order(
            status=PendingOrderStatus.DISPATCHING,
            dispatched_age_sec=120,
        ),
    ])
    n = q.sweep_expired(dispatching_max_age_sec=60)
    assert n == 1
    assert q.get(oid).status == PendingOrderStatus.EXPIRED


def test_sweep_keeps_young_dispatching():
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [
        make_order(
            status=PendingOrderStatus.DISPATCHING,
            dispatched_age_sec=10,
        ),
    ])
    assert q.sweep_expired(dispatching_max_age_sec=60) == 0
    assert q.get(oid).status == PendingOrderStatus.DISPATCHING


def test_sweep_dispatching_falls_back_to_updated_at_when_no_dispatched_at():
    """Edge case: dispatched_at is None (e.g. test fixture); use updated_at."""
    q = InMemoryPendingOrderQueue()
    o = make_order(status=PendingOrderStatus.DISPATCHING, age_sec=120)
    o.dispatched_at = None   # explicitly clear
    [oid] = populate(q, [o])
    # InMemory implementation uses dispatched_at OR updated_at; updated_at
    # was set to age_sec ago, so this should expire.
    n = q.sweep_expired(dispatching_max_age_sec=60)
    assert n == 1


# ================================================================== #
# Mixed bucket sweep
# ================================================================== #
def test_sweep_handles_both_buckets_in_one_call():
    q = InMemoryPendingOrderQueue()
    ids = populate(q, [
        make_order(age_sec=120),                                    # PENDING old
        make_order(age_sec=10),                                     # PENDING young
        make_order(status=PendingOrderStatus.DISPATCHING,
                   dispatched_age_sec=120),                         # DISPATCHING old
        make_order(status=PendingOrderStatus.DISPATCHING,
                   dispatched_age_sec=10),                          # DISPATCHING young
    ])
    n = q.sweep_expired(
        pending_max_age_sec=60, dispatching_max_age_sec=60,
    )
    assert n == 2
    statuses = [q.get(i).status for i in ids]
    assert statuses == [
        PendingOrderStatus.EXPIRED,
        PendingOrderStatus.PENDING,
        PendingOrderStatus.EXPIRED,
        PendingOrderStatus.DISPATCHING,
    ]


# ================================================================== #
# Ignored statuses (terminal etc.)
# ================================================================== #
def test_sweep_ignores_terminal_statuses():
    q = InMemoryPendingOrderQueue()
    ids = populate(q, [
        make_order(status=PendingOrderStatus.FILLED, age_sec=999),
        make_order(status=PendingOrderStatus.REJECTED, age_sec=999),
        make_order(status=PendingOrderStatus.CANCELLED, age_sec=999),
        make_order(status=PendingOrderStatus.EXPIRED, age_sec=999),
        make_order(status=PendingOrderStatus.SUBMITTED, age_sec=999),
        make_order(status=PendingOrderStatus.PARTIALLY_FILLED, age_sec=999),
    ])
    n = q.sweep_expired(pending_max_age_sec=60, dispatching_max_age_sec=60)
    assert n == 0
    # All untouched
    for i, expected in zip(ids, [
        PendingOrderStatus.FILLED, PendingOrderStatus.REJECTED,
        PendingOrderStatus.CANCELLED, PendingOrderStatus.EXPIRED,
        PendingOrderStatus.SUBMITTED, PendingOrderStatus.PARTIALLY_FILLED,
    ]):
        assert q.get(i).status == expected


# ================================================================== #
# Reason captured in last_error + audit event
# ================================================================== #
def test_sweep_records_reason_in_last_error():
    q = InMemoryPendingOrderQueue()
    [oid] = populate(q, [make_order(age_sec=120)])
    q.sweep_expired(pending_max_age_sec=60)
    refreshed = q.get(oid)
    assert refreshed.status == PendingOrderStatus.EXPIRED
    assert "pending" in (refreshed.last_error or "").lower()
    assert "60s threshold" in (refreshed.last_error or "")


def test_sweep_emits_event_with_audit_logger():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    [oid] = populate(q, [make_order(age_sec=120)])
    log.events.clear()   # ignore enqueue events from any earlier test setup

    q.sweep_expired(pending_max_age_sec=60)
    events = log.history(oid)
    assert len(events) == 1
    assert events[0].from_status == PendingOrderStatus.PENDING
    assert events[0].to_status == PendingOrderStatus.EXPIRED
    assert "60s threshold" in (events[0].reason or "")


def test_sweep_dispatching_event_has_worker_crash_hint():
    log = InMemoryEventLogger()
    q = InMemoryPendingOrderQueue(event_logger=log)
    [oid] = populate(q, [
        make_order(status=PendingOrderStatus.DISPATCHING,
                   dispatched_age_sec=120),
    ])
    log.events.clear()
    q.sweep_expired(dispatching_max_age_sec=60)
    e = log.history(oid)[0]
    assert "worker likely crashed" in (e.reason or "")


# ================================================================== #
# Failure isolation
# ================================================================== #
def test_sweep_continues_when_one_update_fails():
    """Simulate a flaky update_status — sweep should keep going."""
    q = InMemoryPendingOrderQueue()
    ids = populate(q, [
        make_order(age_sec=120),
        make_order(age_sec=130),
        make_order(age_sec=140),
    ])

    original_update = q.update_status
    fail_for = {ids[1]}
    def flaky_update(order_id, status, **kw):
        if order_id in fail_for:
            raise RuntimeError("simulated DB blip")
        return original_update(order_id, status, **kw)
    q.update_status = flaky_update  # type: ignore[assignment]

    n = q.sweep_expired(pending_max_age_sec=60)
    assert n == 2   # the two that didn't fail
    assert q.get(ids[0]).status == PendingOrderStatus.EXPIRED
    assert q.get(ids[1]).status == PendingOrderStatus.PENDING   # failed update
    assert q.get(ids[2]).status == PendingOrderStatus.EXPIRED


# ================================================================== #
# CLI argument parsing
# ================================================================== #
def test_cli_rejects_both_thresholds_zero(capsys):
    """Both thresholds default to 0 → caller must opt in to at least one."""
    from execution.pending_orders.cli.sweep import main
    rc = main([])
    assert rc == 2


def test_cli_one_shot_pending(monkeypatch):
    """Smoke: CLI parses args and calls sweep once, exits 0."""
    from execution.pending_orders.cli.sweep import main
    from execution.pending_orders.cli import sweep as sweep_cli

    captured = {}
    class FakeQueue:
        def sweep_expired(self, *, pending_max_age_sec, dispatching_max_age_sec):
            captured["pending"] = pending_max_age_sec
            captured["dispatching"] = dispatching_max_age_sec
            return 0
    monkeypatch.setattr(sweep_cli, "build_queue", lambda settings: FakeQueue())

    rc = main(["--pending-max-age", "300"])
    assert rc == 0
    assert captured["pending"] == 300
    assert captured["dispatching"] == 0


def test_cli_exits_on_noop_queue(monkeypatch, caplog):
    """If queue is NoOp (no DB), CLI exits 1 with helpful error."""
    from execution.pending_orders.cli.sweep import main
    from execution.pending_orders.cli import sweep as sweep_cli

    monkeypatch.setattr(
        sweep_cli, "build_queue", lambda settings: NoOpPendingOrderQueue(),
    )
    rc = main(["--pending-max-age", "60"])
    assert rc == 1
