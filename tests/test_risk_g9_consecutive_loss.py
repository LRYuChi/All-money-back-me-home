"""Tests for daily_pnl_history + ConsecutiveLossDaysGuard (round 22)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    ConsecutiveLossDaysGuard,
    GuardContext,
    GuardPipeline,
    GuardResult,
    InMemoryPnLAggregator,
    NoOpPnLAggregator,
)
from risk.builtin_guards import _trailing_loss_streak


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(notional=500.0) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1",
        symbol="crypto:OKX:BTC/USDT:USDT",
        side="long",
        target_notional_usd=notional,
        mode="shadow",
    )


# ================================================================== #
# daily_pnl_history on InMemory
# ================================================================== #
def test_daily_history_returns_empty_for_zero_days():
    agg = InMemoryPnLAggregator()
    assert agg.daily_pnl_history(days=0) == []


def test_daily_history_excludes_today():
    """Today is in-progress; only completed days included."""
    n = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    agg = InMemoryPnLAggregator([
        # Today (2026-04-25): not counted
        (datetime(2026, 4, 25, 1, 0, tzinfo=timezone.utc), 999.0),
        # Yesterday (2026-04-24): counted in days=1+
        (datetime(2026, 4, 24, 1, 0, tzinfo=timezone.utc), 10.0),
    ])
    history = agg.daily_pnl_history(days=1, now=n)
    assert history == [10.0]


def test_daily_history_returns_in_oldest_first_order():
    n = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    agg = InMemoryPnLAggregator([
        # Day -3 (2026-04-22): -50
        (datetime(2026, 4, 22, 6, 0, tzinfo=timezone.utc), -50.0),
        # Day -2 (2026-04-23): +30
        (datetime(2026, 4, 23, 6, 0, tzinfo=timezone.utc), +30.0),
        # Day -1 (2026-04-24): -20
        (datetime(2026, 4, 24, 6, 0, tzinfo=timezone.utc), -20.0),
    ])
    history = agg.daily_pnl_history(days=3, now=n)
    # [oldest..newest]: -3day, -2day, -1day
    assert history == [-50.0, +30.0, -20.0]


def test_daily_history_aggregates_intra_day():
    """Multiple trades same UTC day → summed."""
    n = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    agg = InMemoryPnLAggregator([
        (datetime(2026, 4, 24, 1, 0, tzinfo=timezone.utc), 10.0),
        (datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc), -5.0),
        (datetime(2026, 4, 24, 23, 0, tzinfo=timezone.utc), +15.0),
    ])
    assert agg.daily_pnl_history(days=1, now=n) == [20.0]


def test_daily_history_zero_for_inactive_days():
    """Day with no trades returns 0, not missing."""
    n = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    agg = InMemoryPnLAggregator([
        (datetime(2026, 4, 23, 6, 0, tzinfo=timezone.utc), 10.0),
    ])
    history = agg.daily_pnl_history(days=3, now=n)
    # [-3day=0, -2day=10, -1day=0]
    assert history == [0.0, 10.0, 0.0]


def test_daily_history_noop_returns_empty():
    assert NoOpPnLAggregator().daily_pnl_history(days=5) == []


# ================================================================== #
# _trailing_loss_streak helper
# ================================================================== #
def test_trailing_streak_zero_when_last_positive():
    assert _trailing_loss_streak([-1, -2, +5]) == 0


def test_trailing_streak_counts_from_right_only():
    assert _trailing_loss_streak([-1, +5, -2, -3]) == 2


def test_trailing_streak_full_run():
    assert _trailing_loss_streak([-1, -2, -3]) == 3


def test_trailing_streak_empty():
    assert _trailing_loss_streak([]) == 0


def test_trailing_streak_zero_value_breaks_streak():
    """A 0 PnL day is non-negative → not a loss; streak stops there."""
    assert _trailing_loss_streak([-1, 0, -2, -3]) == 2


# ================================================================== #
# G9 ConsecutiveLossDaysGuard
# ================================================================== #
def test_g9_construction_requires_aggregator():
    with pytest.raises(ValueError, match="pnl_aggregator"):
        ConsecutiveLossDaysGuard()


def test_g9_construction_requires_positive_threshold():
    agg = InMemoryPnLAggregator()
    with pytest.raises(ValueError, match=">= 1"):
        ConsecutiveLossDaysGuard(max_consecutive_losses=0, pnl_aggregator=agg)


def test_g9_allows_when_aggregator_returns_fewer_than_n_days():
    """A backend that can't supply N days of history → ALLOW (insufficient)."""
    class ShortHistoryAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-100.0]   # only 1 day, but G9 wants 3
    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=ShortHistoryAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "available" in d.reason


def test_g9_allows_when_all_zero_pnl_days():
    """N days of zero PnL = quiet days, NOT a loss streak → ALLOW."""
    agg = InMemoryPnLAggregator()  # no trades; daily_pnl_history returns [0,0,0]
    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=agg)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g9_denies_on_3_day_loss_streak():
    n = datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc)
    agg = InMemoryPnLAggregator([
        (datetime(2026, 4, 22, 6, 0, tzinfo=timezone.utc), -100.0),
        (datetime(2026, 4, 23, 6, 0, tzinfo=timezone.utc), -50.0),
        (datetime(2026, 4, 24, 6, 0, tzinfo=timezone.utc), -25.0),
    ])
    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=agg)

    # Inject `now` via monkeypatch — but easier: provide via aggregator
    # Actually G9 doesn't take `now`; relies on aggregator's default.
    # Use monkeypatch of datetime.now in a controlled way:
    import risk.builtin_guards
    # Workaround: directly call with the aggregator's `now=` overload
    history = agg.daily_pnl_history(days=3, now=n)
    assert history == [-100.0, -50.0, -25.0]
    # Now inject the same history via a wrapper
    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return history[-days:]

    g2 = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg())
    d = g2.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY
    assert "consecutive losing days" in d.reason


def test_g9_allows_when_one_day_breaks_streak():
    """3-day check, day -2 was profitable → no 3-streak → ALLOW."""
    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-100, +20, -50][-days:]

    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    # Detail reports current trailing streak (day -1 = -50, only 1)
    assert d.detail["recent_losses_streak"] == 1


def test_g9_allows_when_zero_pnl_day_breaks_streak():
    """0 PnL counts as non-loss → breaks the streak."""
    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-100, 0.0, -50][-days:]

    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g9_aggregator_failure_fails_open():
    class BadAgg:
        def daily_pnl_history(self, *, days, now=None):
            raise ConnectionError("DB down")

    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=BadAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "fail-open" in d.reason


def test_g9_does_not_scale():
    class FixedAgg:
        def daily_pnl_history(self, *, days, now=None):
            return [-1, -1, -1][-days:]
    g = ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=FixedAgg())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY
    assert d.scaled_size_usd is None


# ================================================================== #
# G9 in pipeline (after G8 — natural order)
# ================================================================== #
def test_g9_in_pipeline_after_g8():
    """Pipeline runs G8 first (today's loss), then G9 (multi-day streak).
    Both can independently trip; either denies."""
    from risk import DailyLossCircuitBreakerGuard

    class FixedAgg:
        def realised_today_usd(self, *, now=None):
            return -10.0  # today small loss, under G8 threshold
        def realised_window_usd(self, *, hours, now=None):
            return -10.0
        def daily_pnl_history(self, *, days, now=None):
            return [-100, -50, -25][-days:]  # 3-day losing streak

    agg = FixedAgg()
    pipeline = GuardPipeline([
        DailyLossCircuitBreakerGuard(loss_threshold_pct=0.05, pnl_aggregator=agg),
        ConsecutiveLossDaysGuard(max_consecutive_losses=3, pnl_aggregator=agg),
    ])
    run = pipeline.evaluate(make_order(), GuardContext(capital_usd=10_000))
    # G8 allows (today's loss only $10 vs $500 threshold)
    # G9 denies (3-day streak)
    assert not run.accepted
    assert run.decisions[-1].guard_name == "consecutive_loss_cb"
