"""Tests for guard side-effect handlers + worker integration (round 26)."""
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
    ConsecutiveLossDaysGuard,
    GuardContext,
    GuardDecision,
    GuardPipeline,
    GuardResult,
    InMemoryPnLAggregator,
    LatencyBudgetGuard,
    MinSizeGuard,
    chain_handlers,
    make_g9_strategy_disabler,
)
from strategy_engine import InMemoryStrategyRegistry


# ================================================================== #
# Helpers
# ================================================================== #
VALID_YAML_TEMPLATE = """
id: {sid}
market: crypto
symbol: BTC
timeframe: 1h
enabled: true
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
"""


def make_order(strategy="s1", notional=500.0, mode="shadow") -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy,
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode=mode,
    )


def static_ctx(capital=10_000, age=5.0):
    ctx = GuardContext(capital_usd=capital, signal_age_seconds=age)
    return lambda order: ctx


def seed_registry(*sids: str) -> InMemoryStrategyRegistry:
    reg = InMemoryStrategyRegistry()
    for sid in sids:
        reg.upsert(VALID_YAML_TEMPLATE.format(sid=sid))
    return reg


def fake_g9_decisions():
    """Mimic the GuardDecision shape that the pipeline produces on G9 trip."""
    return [
        GuardDecision("latency", GuardResult.ALLOW),
        GuardDecision(
            "consecutive_loss_cb", GuardResult.DENY,
            reason="3 consecutive losing days ([-100, -50, -25]) — human review required",
        ),
    ]


# ================================================================== #
# make_g9_strategy_disabler — direct handler tests
# ================================================================== #
def test_disabler_flips_strategy_when_g9_denies():
    reg = seed_registry("s1")
    handler = make_g9_strategy_disabler(reg)
    handler(make_order(strategy="s1"), fake_g9_decisions())
    assert reg.get("s1").parsed.enabled is False


def test_disabler_writes_audit_row_with_actor_and_reason():
    reg = seed_registry("s1")
    handler = make_g9_strategy_disabler(reg)
    handler(make_order(strategy="s1"), fake_g9_decisions())
    history = reg.enable_history("s1")
    assert len(history) == 1
    ev = history[0]
    assert ev.enabled is False
    assert ev.actor == "guard:consecutive_loss_cb"
    assert "G9 trip:" in ev.reason
    assert "3 consecutive losing days" in ev.reason


def test_disabler_does_nothing_when_no_g9_in_decisions():
    reg = seed_registry("s1")
    handler = make_g9_strategy_disabler(reg)
    decisions = [
        GuardDecision("min_size", GuardResult.DENY, reason="too small"),
    ]
    handler(make_order(strategy="s1"), decisions)
    assert reg.get("s1").parsed.enabled is True
    assert reg.enable_history("s1") == []


def test_disabler_does_nothing_when_g9_is_allow_not_deny():
    reg = seed_registry("s1")
    handler = make_g9_strategy_disabler(reg)
    decisions = [GuardDecision("consecutive_loss_cb", GuardResult.ALLOW)]
    handler(make_order(strategy="s1"), decisions)
    assert reg.get("s1").parsed.enabled is True


def test_disabler_idempotent_skips_already_disabled():
    """Second G9 trip on already-disabled strategy → no extra audit row."""
    reg = seed_registry("s1")
    reg.set_enabled("s1", False, reason="manually disabled before test")
    initial_history_len = len(reg.enable_history("s1"))

    handler = make_g9_strategy_disabler(reg)
    handler(make_order(strategy="s1"), fake_g9_decisions())

    assert len(reg.enable_history("s1")) == initial_history_len


def test_disabler_records_every_trip_when_idempotency_off():
    reg = seed_registry("s1")
    reg.set_enabled("s1", False, reason="prior disable")
    handler = make_g9_strategy_disabler(reg, only_if_currently_enabled=False)
    handler(make_order(strategy="s1"), fake_g9_decisions())
    # Should have prior disable + the G9 trip = 2 events
    assert len(reg.enable_history("s1")) == 2


def test_disabler_warns_when_strategy_missing():
    """Order references a strategy not in registry — log + skip, don't raise."""
    reg = seed_registry()  # empty
    handler = make_g9_strategy_disabler(reg)
    # Should not raise
    handler(make_order(strategy="ghost_strategy"), fake_g9_decisions())


def test_disabler_swallows_set_enabled_failure():
    """If set_enabled raises (DB down etc.), handler logs but doesn't propagate."""
    reg = seed_registry("s1")

    def boom(*args, **kwargs):
        raise ConnectionError("DB down")
    reg.set_enabled = boom  # type: ignore[assignment]

    handler = make_g9_strategy_disabler(reg)
    # Must not raise
    handler(make_order(strategy="s1"), fake_g9_decisions())


def test_disabler_uses_custom_actor():
    reg = seed_registry("s1")
    handler = make_g9_strategy_disabler(reg, actor="auto:circuit_breaker")
    handler(make_order(strategy="s1"), fake_g9_decisions())
    assert reg.enable_history("s1")[0].actor == "auto:circuit_breaker"


# ================================================================== #
# chain_handlers
# ================================================================== #
def test_chain_handlers_calls_each_in_order():
    seen = []
    def h1(o, d): seen.append("h1")
    def h2(o, d): seen.append("h2")
    chain = chain_handlers(h1, h2)
    chain(make_order(), [])
    assert seen == ["h1", "h2"]


def test_chain_handlers_continues_after_one_raises():
    seen = []
    def h1(o, d): raise RuntimeError("bad")
    def h2(o, d): seen.append("h2")
    chain = chain_handlers(h1, h2)
    chain(make_order(), [])
    assert seen == ["h2"]


def test_chain_handlers_empty_is_noop():
    chain = chain_handlers()
    chain(make_order(), [])  # no error


# ================================================================== #
# Worker integration
# ================================================================== #
def test_worker_invokes_handler_on_guard_deny():
    """Realistic: G3 MinSize denies, handler is called even though it's not G9."""
    seen = []
    def handler(order, decisions):
        seen.append((order.strategy_id, [d.guard_name for d in decisions]))

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(notional=5))   # under MinSize
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([MinSizeGuard()]),
        context_provider=static_ctx(),
        side_effect_handler=handler,
    )
    w.process_one()
    assert len(seen) == 1
    assert seen[0][0] == "s1"
    assert "min_size" in seen[0][1]
    assert w.stats()["side_effects_invoked"] == 1


def test_worker_does_not_invoke_handler_on_allow():
    seen = []
    def handler(order, decisions):
        seen.append(order.id)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(notional=500))   # passes MinSize
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([MinSizeGuard(default_min_usd=10)]),
        context_provider=static_ctx(),
        side_effect_handler=handler,
    )
    w.process_one()
    assert seen == []
    assert w.stats()["side_effects_invoked"] == 0


def test_worker_swallows_handler_exceptions():
    """A buggy handler must not crash the worker."""
    def bad_handler(o, d):
        raise RuntimeError("handler bug")

    q = InMemoryPendingOrderQueue()
    o = make_order(notional=5)
    q.enqueue(o)
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([MinSizeGuard()]),
        context_provider=static_ctx(),
        side_effect_handler=bad_handler,
    )
    w.process_one()   # must not raise
    refreshed = q.get(o.id)
    assert refreshed.status == PendingOrderStatus.REJECTED
    assert w.stats()["side_effect_errors"] == 1


def test_worker_pipeline_crash_does_not_invoke_handler():
    """Pipeline-crash REJECTs intentionally don't fire side effects —
    no real decisions to react to."""
    def bad_ctx(order):
        raise RuntimeError("ctx blew up")

    seen = []
    def handler(o, d):
        seen.append(o)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order())
    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=GuardPipeline([LatencyBudgetGuard()]),
        context_provider=bad_ctx,
        side_effect_handler=handler,
    )
    w.process_one()
    assert seen == []
    assert w.stats()["side_effects_invoked"] == 0


def test_worker_handler_without_pipeline_raises_at_construction():
    q = InMemoryPendingOrderQueue()
    with pytest.raises(ValueError, match="side_effect_handler requires risk_pipeline"):
        PendingOrderWorker(
            q, LogOnlyDispatcher(),
            side_effect_handler=lambda o, d: None,
        )


# ================================================================== #
# End-to-end: G9 → strategy disabled
# ================================================================== #
def test_e2e_g9_trip_auto_disables_strategy():
    """Pipeline includes G9, agg returns 3 losing days, disabler attached
    → strategy ends up disabled in registry after one order."""
    reg = seed_registry("s_btc")

    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-100, -50, -25][-days:]

    pipeline = GuardPipeline([
        ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg()),
    ])
    handler = make_g9_strategy_disabler(reg)

    q = InMemoryPendingOrderQueue()
    o = make_order(strategy="s_btc")
    q.enqueue(o)

    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=pipeline,
        context_provider=static_ctx(),
        side_effect_handler=handler,
    )
    w.process_one()

    # Order rejected
    assert q.get(o.id).status == PendingOrderStatus.REJECTED
    # Strategy disabled
    assert reg.get("s_btc").parsed.enabled is False
    # Audit trail captured the G9 reason
    history = reg.enable_history("s_btc")
    assert len(history) == 1
    assert history[0].actor == "guard:consecutive_loss_cb"
    assert "consecutive losing days" in history[0].reason


def test_e2e_subsequent_g9_trip_does_not_duplicate_audit():
    """Second order from the same strategy also trips G9 — but strategy
    is already disabled, so handler skips. Audit log stays at 1 row."""
    reg = seed_registry("s_btc")

    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-100, -50, -25][-days:]

    pipeline = GuardPipeline([
        ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg()),
    ])
    handler = make_g9_strategy_disabler(reg)

    q = InMemoryPendingOrderQueue()
    q.enqueue(make_order(strategy="s_btc"))
    q.enqueue(make_order(strategy="s_btc"))

    w = PendingOrderWorker(
        q, LogOnlyDispatcher(),
        risk_pipeline=pipeline,
        context_provider=static_ctx(),
        side_effect_handler=handler,
    )
    w.process_one()
    w.process_one()

    history = reg.enable_history("s_btc")
    assert len(history) == 1   # only the first trip recorded
    assert reg.get("s_btc").parsed.enabled is False
    assert w.stats()["side_effects_invoked"] == 2   # handler ran twice
