"""Tests for PnLAggregator + DailyLossCircuitBreakerGuard (round 20)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    DailyLossCircuitBreakerGuard,
    GuardContext,
    GuardPipeline,
    GuardResult,
    InMemoryPnLAggregator,
    NoOpPnLAggregator,
    build_pnl_aggregator,
    day_boundary_utc,
)


# ================================================================== #
# day_boundary_utc
# ================================================================== #
def test_day_boundary_returns_midnight_utc():
    n = datetime(2026, 4, 25, 14, 30, 7, tzinfo=timezone.utc)
    b = day_boundary_utc(n)
    assert b == datetime(2026, 4, 25, 0, 0, 0, tzinfo=timezone.utc)


def test_day_boundary_default_uses_now(monkeypatch):
    b = day_boundary_utc()
    # Boundary is in the past or = now
    assert b <= datetime.now(timezone.utc)
    assert b.hour == 0 and b.minute == 0 and b.second == 0


# ================================================================== #
# NoOpPnLAggregator
# ================================================================== #
def test_noop_returns_zero():
    agg = NoOpPnLAggregator()
    assert agg.realised_today_usd() == 0.0
    assert agg.realised_window_usd(hours=24) == 0.0


# ================================================================== #
# InMemoryPnLAggregator
# ================================================================== #
def test_inmemory_sums_today_only():
    today = datetime.now(timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    yesterday = today - timedelta(days=1)
    agg = InMemoryPnLAggregator([
        # Today: 10 + (-5) + 20 = 25
        (today.replace(hour=1, minute=0), 10.0),
        (today.replace(hour=7, minute=0), -5.0),
        (today.replace(hour=13, minute=0), 20.0),
        # Yesterday late evening: should NOT count
        (yesterday.replace(hour=22, minute=0), 100.0),
    ])
    assert agg.realised_today_usd(now=today) == 25.0


def test_inmemory_window_hours():
    today = datetime.now(timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    agg = InMemoryPnLAggregator([
        # 1h window from `today` includes only the 13:00 trade
        (today.replace(hour=13, minute=0), 20.0),
        (today.replace(hour=12, minute=0), 50.0),
    ])
    assert agg.realised_window_usd(hours=1, now=today) == 20.0
    assert agg.realised_window_usd(hours=3, now=today) == 70.0


def test_inmemory_naive_ts_treated_as_utc():
    """Naive datetimes added are coerced to UTC for arithmetic safety."""
    today = datetime.now(timezone.utc).replace(
        hour=14, minute=0, second=0, microsecond=0,
    )
    agg = InMemoryPnLAggregator()
    # Naive datetime at start of today
    naive_today_morning = datetime(today.year, today.month, today.day, 1, 0)
    agg.add(naive_today_morning, 42.0)
    assert agg.realised_today_usd(now=today) == 42.0


# ================================================================== #
# DailyLossCircuitBreakerGuard
# ================================================================== #
def make_order(notional=500.0) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1",
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode="shadow",
    )


def test_g8_construction_requires_aggregator():
    with pytest.raises(ValueError, match="pnl_aggregator"):
        DailyLossCircuitBreakerGuard()


def test_g8_allows_when_no_loss():
    """Today's PnL is positive — guard allows."""
    agg = InMemoryPnLAggregator()
    # No trades → 0 PnL
    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g8_allows_when_loss_under_threshold():
    """Loss = 4% of capital, threshold = 5% → allow."""
    # Use today (UTC) as anchor — hardcoded date ages out at midnight UTC,
    # causing PnL to be filed under "yesterday" and tests to fail.
    n = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    # Capital 10000, threshold 5% = -500. Loss -400 = under threshold.
    agg = InMemoryPnLAggregator([
        (n - timedelta(hours=2), -400.0),
    ])
    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert d.detail["realised_pnl_today"] < 0


def test_g8_denies_when_loss_at_threshold():
    """Loss exactly equals threshold → DENY (rule is `<=`)."""
    # Use today (UTC) as anchor — hardcoded date ages out at midnight UTC,
    # causing PnL to be filed under "yesterday" and tests to fail.
    n = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    agg = InMemoryPnLAggregator([
        (n - timedelta(hours=2), -500.0),  # exactly -5% of 10k
    ])
    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY
    assert "circuit breaker" in d.reason
    assert d.detail["realised_pnl_today"] == -500.0


def test_g8_denies_when_loss_exceeds_threshold():
    # Use today (UTC) as anchor — hardcoded date ages out at midnight UTC,
    # causing PnL to be filed under "yesterday" and tests to fail.
    n = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    agg = InMemoryPnLAggregator([
        (n - timedelta(hours=2), -800.0),  # -8% of 10k
    ])
    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY


def test_g8_aggregator_failure_is_fail_open():
    """If the aggregator throws, allow (don't block all trades on a flaky DB)."""
    class BadAgg:
        def realised_today_usd(self, *, now=None):
            raise ConnectionError("DB down")
        def realised_window_usd(self, *, hours, now=None):
            raise ConnectionError("DB down")

    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=BadAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "fail-open" in d.reason


def test_g8_does_not_scale():
    """G8 is binary: stop trading or don't. No SCALE."""
    # Use today (UTC) as anchor — hardcoded date ages out at midnight UTC,
    # causing PnL to be filed under "yesterday" and tests to fail.
    n = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    agg = InMemoryPnLAggregator([
        (n - timedelta(hours=2), -1000.0),
    ])
    g = DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY
    assert d.scaled_size_usd is None


# ================================================================== #
# Integration: G8 in a pipeline
# ================================================================== #
def test_g8_integrates_with_pipeline_first():
    """G8 first in pipeline: tripped → no other guards run."""
    from risk import LatencyBudgetGuard

    # Use today (UTC) as anchor — hardcoded date ages out at midnight UTC,
    # causing PnL to be filed under "yesterday" and tests to fail.
    n = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    agg = InMemoryPnLAggregator([
        (n - timedelta(hours=2), -1000.0),  # heavy loss
    ])
    pipeline = GuardPipeline([
        DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg),
        LatencyBudgetGuard(),  # would otherwise fire on no-age too
    ])
    run = pipeline.evaluate(
        make_order(),
        GuardContext(capital_usd=10_000, signal_age_seconds=5.0),
    )
    assert not run.accepted
    assert run.decisions[0].guard_name == "daily_loss_cb"
    # Latency guard should NOT have been called (short-circuit)
    assert len(run.decisions) == 1


# ================================================================== #
# build_pnl_aggregator factory
# ================================================================== #
def test_factory_noop_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""
    agg = build_pnl_aggregator(S())
    assert isinstance(agg, NoOpPnLAggregator)


def test_factory_postgres_when_dsn_set():
    from risk import PostgresPnLAggregator
    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""
    agg = build_pnl_aggregator(S())
    assert isinstance(agg, PostgresPnLAggregator)
