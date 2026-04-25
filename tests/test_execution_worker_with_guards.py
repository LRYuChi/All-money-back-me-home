"""Tests for PendingOrderWorker + GuardPipeline integration (round 19)."""
from __future__ import annotations

import pytest

from execution.pending_orders import (
    InMemoryPendingOrderQueue,
    LogOnlyDispatcher,
    PendingOrder,
    PendingOrderStatus,
    PendingOrderWorker,
)
from risk import (
    GlobalExposureGuard,
    GuardContext,
    GuardPipeline,
    LatencyBudgetGuard,
    MinSizeGuard,
    PerStrategyExposureGuard,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(strategy="s1", notional=500.0, mode="shadow") -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy,
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode=mode,
    )


def static_ctx(
    capital=10_000,
    by_strategy=None,
    global_notional=0.0,
    age=5.0,
):
    """Returns a GuardContext that ignores the order argument — used
    when test pre-computes the context."""
    ctx = GuardContext(
        capital_usd=capital,
        open_notional_by_strategy=by_strategy or {},
        global_open_notional=global_notional,
        signal_age_seconds=age,
    )
    return lambda order: ctx


# ================================================================== #
# Constructor invariants
# ================================================================== #
def test_pipeline_without_provider_rejected():
    q = InMemoryPendingOrderQueue()
    pipeline = GuardPipeline([LatencyBudgetGuard()])
    with pytest.raises(ValueError, match="context_provider"):
        PendingOrderWorker(q, LogOnlyDispatcher(), risk_pipeline=pipeline)


def test_no_pipeline_means_legacy_behaviour():
    """Without a pipeline, worker behaves exactly as round 17 — no guards
    consulted, no new stats."""
    q = InMemoryPendingOrderQueue()
    o = make_order(notional=5)  # would fail MinSize if guard wired
    q.enqueue(o)
    w = PendingOrderWorker(q, LogOnlyDispatcher())
    w.process_one()
    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.FILLED
    assert w.stats()["guard_denies"] == 0


# ================================================================== #
# DENY path — order rejected with guard reason
# ================================================================== #
def test_deny_marks_rejected_with_guard_reason():
    q = InMemoryPendingOrderQueue()
    o = make_order(notional=5)   # below MinSize default 10
    q.enqueue(o)

    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([MinSizeGuard(default_min_usd=10)]),
        context_provider=static_ctx(),
    )
    w.process_one()

    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert refreshed.last_error.startswith("guard:min_size")
    assert "below min" in refreshed.last_error
    assert w.stats()["guard_denies"] == 1
    assert w.stats()["rejected"] == 1
    assert w.stats()["filled"] == 0


def test_first_deny_short_circuits_dispatch():
    """A guard DENY means the dispatcher is NEVER called."""
    dispatcher_calls = {"n": 0}

    class CountingDispatcher:
        @property
        def mode(self):
            return "shadow"
        def dispatch(self, order):
            dispatcher_calls["n"] += 1
            from execution.pending_orders import DispatchResult
            return DispatchResult(terminal_status=PendingOrderStatus.FILLED)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(notional=5))   # below MinSize
    w = PendingOrderWorker(
        q, CountingDispatcher(),
        risk_pipeline=GuardPipeline([MinSizeGuard()]),
        context_provider=static_ctx(),
    )
    w.process_one()
    assert dispatcher_calls["n"] == 0


def test_latency_deny_uses_signal_age():
    q = InMemoryPendingOrderQueue()
    o = make_order(notional=500)
    q.enqueue(o)
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([LatencyBudgetGuard(budget_seconds=15)]),
        context_provider=static_ctx(age=20.0),   # stale
    )
    w.process_one()
    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert "latency" in refreshed.last_error


# ================================================================== #
# SCALE path — order proceeds with reduced size
# ================================================================== #
def test_scale_uses_reduced_notional_in_dispatch():
    """G4 SCALE down to 500; dispatcher receives the reduced order."""
    received = {}

    class CapturingDispatcher:
        @property
        def mode(self):
            return "shadow"
        def dispatch(self, order):
            received["notional"] = order.target_notional_usd
            from execution.pending_orders import DispatchResult
            return DispatchResult(terminal_status=PendingOrderStatus.FILLED)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(notional=1000))   # request 1000

    w = PendingOrderWorker(
        q, CapturingDispatcher(),
        # Cap 20% of 10k = 2000, used 1500 → room 500 → scale to 500
        risk_pipeline=GuardPipeline([
            PerStrategyExposureGuard(cap_pct_of_capital=0.20),
        ]),
        context_provider=static_ctx(by_strategy={"s1": 1500}),
    )
    w.process_one()
    assert received["notional"] == 500
    assert w.stats()["guard_scales"] == 1
    assert w.stats()["filled"] == 1


def test_scale_then_pass_pipeline_e2e():
    """Realistic 3-guard chain: latency ALLOW + strategy SCALE + global ALLOW."""
    q = InMemoryPendingOrderQueue()
    o = make_order(notional=1000)
    q.enqueue(o)

    pipeline = GuardPipeline([
        LatencyBudgetGuard(budget_seconds=15),
        PerStrategyExposureGuard(cap_pct_of_capital=0.20),
        GlobalExposureGuard(capital_multiplier=1.5),
    ])
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=pipeline,
        context_provider=static_ctx(
            by_strategy={"s1": 1500},   # room 500 → scale
            global_notional=10_000,     # global cap 15k, well under
            age=5.0,
        ),
    )
    w.process_one()
    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.FILLED
    assert refreshed.target_notional_usd == 500   # mutated by SCALE
    assert w.stats()["guard_scales"] == 1


# ================================================================== #
# Pipeline crash → REJECTED (fail safe)
# ================================================================== #
def test_pipeline_crash_treated_as_reject():
    """If guard or context_provider raises, order is REJECTED (don't blast
    through with no risk check)."""
    def bad_ctx(order):
        raise RuntimeError("DB lookup failed")

    q = InMemoryPendingOrderQueue()
    o = make_order(notional=500)
    q.enqueue(o)

    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([LatencyBudgetGuard()]),
        context_provider=bad_ctx,
    )
    w.process_one()
    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert "guard_pipeline_error" in refreshed.last_error
    assert w.stats()["guard_denies"] == 1


# ================================================================== #
# context_provider receives the order (so it can key on strategy/symbol)
# ================================================================== #
def test_context_provider_receives_order():
    seen = []
    def ctx_provider(order):
        seen.append(order.strategy_id)
        return GuardContext(capital_usd=10_000)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(strategy="alpha"))
    q.enqueue(make_order(strategy="beta"))

    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([LatencyBudgetGuard()]),
        context_provider=ctx_provider,
    )
    w.process_one()
    w.process_one()
    assert seen == ["alpha", "beta"]


# ================================================================== #
# E2E with multiple orders + mixed outcomes
# ================================================================== #
def test_e2e_mixed_outcomes_through_pipeline():
    """3 orders: one accepted, one scaled, one denied — all reach correct status."""
    q = InMemoryPendingOrderQueue()
    accepted = make_order(strategy="s_ok", notional=200)
    scaled = make_order(strategy="s_scale", notional=1000)
    denied = make_order(strategy="s_deny", notional=5)
    q.enqueue(accepted)
    q.enqueue(scaled)
    q.enqueue(denied)

    pipeline = GuardPipeline([
        MinSizeGuard(default_min_usd=10),                    # denies 'denied'
        PerStrategyExposureGuard(cap_pct_of_capital=0.20),   # scales 'scaled'
    ])
    # Pre-load existing exposure for s_scale → forces SCALE
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=pipeline,
        context_provider=static_ctx(
            by_strategy={"s_scale": 1500},   # cap 2000, room 500
        ),
    )
    for _ in range(3):
        w.process_one()

    assert q.get(accepted.id).status == PendingOrderStatus.FILLED
    assert q.get(accepted.id).target_notional_usd == 200      # untouched

    assert q.get(scaled.id).status == PendingOrderStatus.FILLED
    assert q.get(scaled.id).target_notional_usd == 500        # scaled

    assert q.get(denied.id).status == PendingOrderStatus.REJECTED
    assert "min_size" in q.get(denied.id).last_error

    s = w.stats()
    assert s["filled"] == 2
    assert s["rejected"] == 1
    assert s["guard_scales"] == 1
    assert s["guard_denies"] == 1
