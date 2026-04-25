"""Tests for risk.guards + risk.builtin_guards."""
from __future__ import annotations

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    GlobalExposureGuard,
    GuardContext,
    GuardDecision,
    GuardPipeline,
    GuardResult,
    LatencyBudgetGuard,
    MinSizeGuard,
    PerMarketExposureGuard,
    PerStrategyExposureGuard,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    strategy="s1",
    symbol="crypto:OKX:BTC/USDT:USDT",
    notional=500.0,
    mode="shadow",
) -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy,
        symbol=symbol,
        side="long",
        target_notional_usd=notional,
        mode=mode,
    )


def make_ctx(
    capital=10_000.0,
    by_strategy=None,
    by_market=None,
    global_notional=0.0,
    age=None,
) -> GuardContext:
    return GuardContext(
        capital_usd=capital,
        open_notional_by_strategy=by_strategy or {},
        open_notional_by_market=by_market or {},
        global_open_notional=global_notional,
        signal_age_seconds=age,
    )


# ================================================================== #
# G1 LatencyBudget
# ================================================================== #
def test_latency_allows_fresh_signal():
    g = LatencyBudgetGuard(budget_seconds=15)
    d = g.check(make_order(), make_ctx(age=5.0))
    assert d.allowed
    assert d.result == GuardResult.ALLOW


def test_latency_denies_stale_signal():
    g = LatencyBudgetGuard(budget_seconds=15)
    d = g.check(make_order(), make_ctx(age=20.0))
    assert d.result == GuardResult.DENY
    assert "old" in d.reason


def test_latency_passes_when_age_unknown():
    """No signal_age_seconds in context → fail open (allow)."""
    g = LatencyBudgetGuard(budget_seconds=15)
    d = g.check(make_order(), make_ctx(age=None))
    assert d.allowed


# ================================================================== #
# G3 MinSize
# ================================================================== #
def test_minsize_allows_above_threshold():
    g = MinSizeGuard(default_min_usd=10)
    d = g.check(make_order(notional=15), make_ctx())
    assert d.allowed


def test_minsize_denies_below_threshold():
    g = MinSizeGuard(default_min_usd=10)
    d = g.check(make_order(notional=5), make_ctx())
    assert d.result == GuardResult.DENY


def test_minsize_per_symbol_override_takes_precedence():
    g = MinSizeGuard(
        default_min_usd=10,
        min_by_symbol={"crypto:OKX:BTC/USDT:USDT": 50},
    )
    # Below the per-symbol min (50), above the default (10) → DENY
    d = g.check(make_order(notional=20), make_ctx())
    assert d.result == GuardResult.DENY


# ================================================================== #
# G4 PerStrategyExposure
# ================================================================== #
def test_strategy_exposure_allows_under_cap():
    g = PerStrategyExposureGuard(cap_pct_of_capital=0.20)  # 20% of 10k = 2000
    d = g.check(make_order(notional=500), make_ctx(capital=10_000))
    assert d.result == GuardResult.ALLOW


def test_strategy_exposure_scales_when_partial_room():
    g = PerStrategyExposureGuard(cap_pct_of_capital=0.20, deny_floor_pct=0.10)
    # Cap = 2000, already 1500 open → room = 500. Order 1000 → scale to 500.
    d = g.check(
        make_order(notional=1000),
        make_ctx(capital=10_000, by_strategy={"s1": 1500}),
    )
    assert d.result == GuardResult.SCALE
    assert d.scaled_size_usd == 500


def test_strategy_exposure_denies_when_at_cap():
    g = PerStrategyExposureGuard(cap_pct_of_capital=0.20)
    d = g.check(
        make_order(notional=500),
        make_ctx(capital=10_000, by_strategy={"s1": 2000}),  # already at cap
    )
    assert d.result == GuardResult.DENY


def test_strategy_exposure_denies_when_scale_would_be_below_floor():
    """If room is < floor% of original request, DENY rather than scale to dust."""
    g = PerStrategyExposureGuard(cap_pct_of_capital=0.20, deny_floor_pct=0.10)
    # Cap 2000, used 1980 → room 20. Request 1000. floor = 100. 20 < 100 → DENY.
    d = g.check(
        make_order(notional=1000),
        make_ctx(capital=10_000, by_strategy={"s1": 1980}),
    )
    assert d.result == GuardResult.DENY


# ================================================================== #
# G5 PerMarketExposure
# ================================================================== #
def test_market_exposure_allows_under_cap():
    g = PerMarketExposureGuard(default_cap_pct=0.50)
    d = g.check(make_order(notional=1000), make_ctx(capital=10_000))
    assert d.result == GuardResult.ALLOW


def test_market_exposure_parses_symbol_market_prefix():
    g = PerMarketExposureGuard(
        default_cap_pct=0.50,
        cap_pct_by_market={"crypto": 0.30, "us": 0.20},
    )
    # crypto cap 30% × 10k = 3000. By-market crypto open 1000 → room 2000.
    d = g.check(
        make_order(notional=500, symbol="crypto:OKX:BTC/USDT:USDT"),
        make_ctx(capital=10_000, by_market={"crypto": 1000}),
    )
    assert d.result == GuardResult.ALLOW


def test_market_exposure_scales_when_partial_room():
    g = PerMarketExposureGuard(default_cap_pct=0.50)
    # Cap 5000, open 4500, room 500. Request 1000 → scale to 500.
    d = g.check(
        make_order(notional=1000),
        make_ctx(capital=10_000, by_market={"crypto": 4500}),
    )
    assert d.result == GuardResult.SCALE
    assert d.scaled_size_usd == 500


def test_market_exposure_denies_when_at_cap():
    g = PerMarketExposureGuard(default_cap_pct=0.30)
    d = g.check(
        make_order(notional=500),
        make_ctx(capital=10_000, by_market={"crypto": 3000}),
    )
    assert d.result == GuardResult.DENY


def test_market_exposure_uses_default_when_market_missing_from_caps():
    g = PerMarketExposureGuard(
        default_cap_pct=0.40, cap_pct_by_market={"us": 0.20},
    )
    # crypto isn't in cap_pct_by_market → uses default 0.40
    d = g.check(
        make_order(notional=3000, symbol="crypto:OKX:BTC/USDT:USDT"),
        make_ctx(capital=10_000, by_market={"crypto": 0}),
    )
    # Cap 4000 > request 3000 → ALLOW
    assert d.result == GuardResult.ALLOW


# ================================================================== #
# G6 GlobalExposure
# ================================================================== #
def test_global_exposure_allows_under_cap():
    g = GlobalExposureGuard(capital_multiplier=1.5)
    # Cap = 1.5 × 10k = 15k. Current 5k + request 1k = 6k → allow.
    d = g.check(
        make_order(notional=1000),
        make_ctx(capital=10_000, global_notional=5000),
    )
    assert d.result == GuardResult.ALLOW


def test_global_exposure_denies_when_over_cap():
    g = GlobalExposureGuard(capital_multiplier=1.5)
    # Cap = 15k. Current 14k + request 2k = 16k > cap → DENY.
    d = g.check(
        make_order(notional=2000),
        make_ctx(capital=10_000, global_notional=14_000),
    )
    assert d.result == GuardResult.DENY


def test_global_exposure_does_not_scale_only_denies():
    """G6 doesn't scale — taking smaller bites at leverage limit isn't risk-mgmt."""
    g = GlobalExposureGuard(capital_multiplier=1.5)
    d = g.check(
        make_order(notional=2000),
        make_ctx(capital=10_000, global_notional=14_000),
    )
    assert d.result == GuardResult.DENY
    assert d.scaled_size_usd is None


# ================================================================== #
# GuardPipeline
# ================================================================== #
def test_pipeline_allows_when_all_pass():
    pipeline = GuardPipeline([
        LatencyBudgetGuard(budget_seconds=15),
        MinSizeGuard(default_min_usd=10),
    ])
    run = pipeline.evaluate(make_order(notional=100), make_ctx(age=5.0))
    assert run.accepted is True
    assert all(d.allowed for d in run.decisions)
    assert run.final_notional_usd == 100


def test_pipeline_short_circuits_on_first_deny():
    """Once a guard denies, downstream guards aren't called."""
    counter = {"min_size_called": 0}

    class SpyMinSize:
        name = "spy_min_size"
        def check(self, order, ctx):
            counter["min_size_called"] += 1
            return GuardDecision(self.name, GuardResult.ALLOW)

    pipeline = GuardPipeline([
        LatencyBudgetGuard(budget_seconds=15),  # will DENY (age 20 > 15)
        SpyMinSize(),
    ])
    run = pipeline.evaluate(make_order(), make_ctx(age=20.0))
    assert run.accepted is False
    assert counter["min_size_called"] == 0


def test_pipeline_scale_mutates_order_for_subsequent_guards():
    """G4 SCALES, then G6 sees the reduced notional."""
    pipeline = GuardPipeline([
        PerStrategyExposureGuard(cap_pct_of_capital=0.20),  # cap 2000
        GlobalExposureGuard(capital_multiplier=1.5),         # cap 15000
    ])
    # Cap strategy 2000, used 1500 → room 500. Request 1000 → scale to 500.
    # Then G6: global 14_500 + 500 = 15_000 (boundary, > cap fails strictly).
    run = pipeline.evaluate(
        make_order(notional=1000),
        make_ctx(
            capital=10_000,
            by_strategy={"s1": 1500},
            global_notional=14_500,
        ),
    )
    # G4 scaled to 500; G6 sees 500 + 14_500 = 15_000 == cap, rule is `>` so allow
    assert run.accepted is True
    assert run.final_notional_usd == 500
    assert run.decisions[0].result == GuardResult.SCALE


def test_pipeline_invalid_scale_treated_as_deny():
    """A guard returning SCALE with no scaled_size_usd is misbehaving →
    pipeline DENIES for safety."""
    class BadGuard:
        name = "bad"
        def check(self, order, ctx):
            return GuardDecision(self.name, GuardResult.SCALE)  # no scaled_size_usd!

    pipeline = GuardPipeline([BadGuard()])
    run = pipeline.evaluate(make_order(), make_ctx())
    assert run.accepted is False


def test_pipeline_decisions_recorded_in_order():
    pipeline = GuardPipeline([
        LatencyBudgetGuard(),
        MinSizeGuard(),
    ])
    run = pipeline.evaluate(make_order(notional=100), make_ctx(age=5.0))
    assert [d.guard_name for d in run.decisions] == ["latency", "min_size"]


# ================================================================== #
# Cumulative scenario — realistic end-to-end
# ================================================================== #
def test_pipeline_realistic_scenario():
    """Strategy is at 70% of strategy cap, market at 60% of market cap,
    global has plenty of room. Order should scale through both first
    guards and reach the dispatcher."""
    pipeline = GuardPipeline([
        LatencyBudgetGuard(budget_seconds=15),
        MinSizeGuard(default_min_usd=10),
        PerStrategyExposureGuard(cap_pct_of_capital=0.20),
        PerMarketExposureGuard(default_cap_pct=0.50),
        GlobalExposureGuard(capital_multiplier=1.5),
    ])

    # Capital 10k. Strategy cap 2000, used 1400 → room 600.
    # Market cap 5000, used 3000 → room 2000.
    # Global cap 15k, used 10k → room 5k.
    # Request 1000.
    # G4: scale to 600.
    # G5: room 2000, request 600 → ALLOW (no further scale).
    # G6: 10k + 600 = 10.6k < 15k → ALLOW.
    run = pipeline.evaluate(
        make_order(notional=1000),
        make_ctx(
            capital=10_000,
            by_strategy={"s1": 1400},
            by_market={"crypto": 3000},
            global_notional=10_000,
            age=5.0,
        ),
    )
    assert run.accepted is True
    assert run.final_notional_usd == 600
