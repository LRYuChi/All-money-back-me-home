"""Tests for WinRateProvider + KellyPositionGuard (G10, round 30)."""
from __future__ import annotations

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    GuardContext,
    GuardPipeline,
    GuardResult,
    InMemoryWinRateProvider,
    KellyPositionGuard,
    NoOpWinRateProvider,
    PostgresWinRateProvider,
    WinRateStats,
    build_win_rate_provider,
)
from risk.win_rate_provider import _compute_stats


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    strategy="s1",
    symbol="crypto:OKX:BTC/USDT:USDT",
    notional=1000.0,
) -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy, symbol=symbol, side="long",
        target_notional_usd=notional, mode="shadow",
    )


def stats(*, n=100, win=0.55, win_pct=0.02, loss_pct=0.012) -> WinRateStats:
    return WinRateStats(
        n_trades=n, win_rate=win, avg_win_pct=win_pct, avg_loss_pct=loss_pct,
    )


# ================================================================== #
# WinRateStats
# ================================================================== #
def test_stats_construction_validates_n_trades():
    with pytest.raises(ValueError, match="n_trades"):
        WinRateStats(n_trades=-1, win_rate=0.5, avg_win_pct=0.01, avg_loss_pct=0.01)


def test_stats_construction_validates_win_rate_range():
    with pytest.raises(ValueError, match="win_rate"):
        WinRateStats(n_trades=10, win_rate=1.5, avg_win_pct=0.01, avg_loss_pct=0.01)


def test_stats_construction_rejects_negative_avg_returns():
    with pytest.raises(ValueError):
        WinRateStats(n_trades=10, win_rate=0.5, avg_win_pct=-0.01, avg_loss_pct=0.01)


def test_stats_kelly_positive_edge():
    """p=0.6, b=2 → Kelly = (0.6*2 - 0.4) / 2 = 0.4"""
    s = WinRateStats(n_trades=100, win_rate=0.6, avg_win_pct=0.02, avg_loss_pct=0.01)
    assert s.kelly_fraction == pytest.approx(0.4)


def test_stats_kelly_break_even():
    """p=0.5, b=1 → Kelly = (0.5*1 - 0.5) / 1 = 0"""
    s = WinRateStats(n_trades=100, win_rate=0.5, avg_win_pct=0.01, avg_loss_pct=0.01)
    assert s.kelly_fraction == 0.0


def test_stats_kelly_negative_edge():
    """Loser strategy → negative Kelly."""
    s = WinRateStats(n_trades=100, win_rate=0.4, avg_win_pct=0.01, avg_loss_pct=0.02)
    assert s.kelly_fraction < 0


def test_stats_kelly_no_losses_caps_at_one():
    s = WinRateStats(n_trades=10, win_rate=1.0, avg_win_pct=0.05, avg_loss_pct=0.0)
    assert s.kelly_fraction == 1.0


# ================================================================== #
# _compute_stats helper
# ================================================================== #
def test_compute_stats_skips_zero_pnl():
    rows = [(10, 100), (-5, 100), (0, 100)]
    s = _compute_stats(rows)
    assert s.n_trades == 2   # zero-pnl skipped


def test_compute_stats_skips_zero_notional():
    rows = [(10, 100), (5, 0)]
    s = _compute_stats(rows)
    assert s.n_trades == 1


def test_compute_stats_empty_returns_zero_stats():
    s = _compute_stats([])
    assert s.n_trades == 0
    assert s.win_rate == 0.0


def test_compute_stats_correct_aggregates():
    rows = [(20, 1000), (10, 1000), (-15, 1000)]   # 2 wins (2%, 1%), 1 loss (1.5%)
    s = _compute_stats(rows)
    assert s.n_trades == 3
    assert s.win_rate == pytest.approx(2/3)
    assert s.avg_win_pct == pytest.approx(0.015)   # (0.02+0.01)/2
    assert s.avg_loss_pct == pytest.approx(0.015)


# ================================================================== #
# WinRateProvider backends
# ================================================================== #
def test_noop_returns_none():
    assert NoOpWinRateProvider().stats(strategy_id="s1") is None


def test_inmemory_returns_seeded_stats():
    p = InMemoryWinRateProvider()
    p.add(stats(n=100), strategy_id="s1")
    out = p.stats(strategy_id="s1")
    assert out is not None and out.n_trades == 100


def test_inmemory_returns_none_for_unknown_key():
    p = InMemoryWinRateProvider()
    assert p.stats(strategy_id="nope") is None


def test_inmemory_falls_back_to_broader_keys():
    p = InMemoryWinRateProvider()
    p.add(stats(n=50), strategy_id=None, symbol=None)   # global default
    out = p.stats(strategy_id="s1", symbol="BTC")
    assert out is not None and out.n_trades == 50


def test_inmemory_specific_key_wins_over_general():
    p = InMemoryWinRateProvider()
    p.add(stats(n=10), strategy_id=None, symbol=None)
    p.add(stats(n=99), strategy_id="s1", symbol=None)
    out = p.stats(strategy_id="s1", symbol="BTC")
    assert out.n_trades == 99


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_no_dsn():
    class S:
        database_url = ""
    assert isinstance(build_win_rate_provider(S()), NoOpWinRateProvider)


def test_factory_postgres_when_dsn_set():
    class S:
        database_url = "postgresql://x"
    assert isinstance(build_win_rate_provider(S()), PostgresWinRateProvider)


# ================================================================== #
# G10 KellyPositionGuard — construction
# ================================================================== #
def test_g10_construction_requires_provider():
    with pytest.raises(ValueError, match="win_rate_provider"):
        KellyPositionGuard()


def test_g10_construction_rejects_bad_safety_factor():
    p = NoOpWinRateProvider()
    with pytest.raises(ValueError, match="safety_factor"):
        KellyPositionGuard(win_rate_provider=p, safety_factor=0)
    with pytest.raises(ValueError, match="safety_factor"):
        KellyPositionGuard(win_rate_provider=p, safety_factor=1.5)


def test_g10_construction_rejects_bad_min_trades():
    p = NoOpWinRateProvider()
    with pytest.raises(ValueError, match="min_trades"):
        KellyPositionGuard(win_rate_provider=p, min_trades=0)


# ================================================================== #
# G10 — fail-open paths
# ================================================================== #
def test_g10_allows_when_provider_returns_none():
    g = KellyPositionGuard(win_rate_provider=NoOpWinRateProvider())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "no win_rate stats" in d.reason


def test_g10_allows_when_provider_raises():
    class BadProvider:
        def stats(self, **kw):
            raise ConnectionError("db down")
    g = KellyPositionGuard(win_rate_provider=BadProvider())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "fail-open" in d.reason


def test_g10_allows_when_sample_too_small():
    p = InMemoryWinRateProvider()
    p.add(stats(n=10), strategy_id="s1")   # below default min=30
    g = KellyPositionGuard(win_rate_provider=p, min_trades=30)
    d = g.check(make_order(strategy="s1"), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "insufficient sample" in d.reason


# ================================================================== #
# G10 — DENY paths
# ================================================================== #
def test_g10_denies_negative_edge():
    p = InMemoryWinRateProvider()
    # 40% winners, 1% wins, 2% losses → strongly negative Kelly
    p.add(
        WinRateStats(n_trades=100, win_rate=0.4, avg_win_pct=0.01, avg_loss_pct=0.02),
        strategy_id="s1",
    )
    g = KellyPositionGuard(win_rate_provider=p)
    d = g.check(make_order(strategy="s1"), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY
    assert "negative-edge" in d.reason


def test_g10_denies_break_even_strategy():
    """Kelly = 0 → DENY (kelly <= 0 branch)."""
    p = InMemoryWinRateProvider()
    p.add(
        WinRateStats(n_trades=100, win_rate=0.5, avg_win_pct=0.01, avg_loss_pct=0.01),
        strategy_id="s1",
    )
    g = KellyPositionGuard(win_rate_provider=p)
    d = g.check(make_order(strategy="s1"), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.DENY


# ================================================================== #
# G10 — ALLOW + SCALE paths
# ================================================================== #
def test_g10_allows_when_request_under_kelly_cap():
    """Kelly = 0.4, safety = 0.25 → cap = 1000. Request 500 → ALLOW."""
    p = InMemoryWinRateProvider()
    p.add(stats(n=100, win=0.6, win_pct=0.02, loss_pct=0.01), strategy_id="s1")
    g = KellyPositionGuard(win_rate_provider=p, safety_factor=0.25)
    d = g.check(
        make_order(strategy="s1", notional=500),
        GuardContext(capital_usd=10_000),
    )
    assert d.result == GuardResult.ALLOW
    assert d.detail["kelly_cap_usd"] == pytest.approx(1000.0)


def test_g10_scales_when_request_exceeds_kelly_cap():
    """Kelly = 0.4, safety = 0.25, capital = 10000 → cap = 1000. Request
    2500 → SCALE to 1000."""
    p = InMemoryWinRateProvider()
    p.add(stats(n=100, win=0.6, win_pct=0.02, loss_pct=0.01), strategy_id="s1")
    g = KellyPositionGuard(win_rate_provider=p, safety_factor=0.25)
    d = g.check(
        make_order(strategy="s1", notional=2500),
        GuardContext(capital_usd=10_000),
    )
    assert d.result == GuardResult.SCALE
    assert d.scaled_size_usd == pytest.approx(1000.0)


def test_g10_denies_when_scaled_below_floor():
    """Kelly = 0.04 (very small edge), safety = 0.25 → cap = 100.
    Request 1000, floor = 100 = exactly floor → SCALE allowed (>=)
    But if cap < floor (e.g. cap=50, floor=100) → DENY."""
    p = InMemoryWinRateProvider()
    # Construct stats giving Kelly ≈ 0.005: p=0.5+ε, b=1
    p.add(
        WinRateStats(n_trades=100, win_rate=0.51, avg_win_pct=0.01, avg_loss_pct=0.0099),
        strategy_id="s1",
    )
    g = KellyPositionGuard(
        win_rate_provider=p, safety_factor=0.25, deny_floor_pct=0.50,
    )
    # Kelly ≈ (0.51*1.0101 - 0.49)/1.0101 ≈ 0.0249
    # Cap = 10000 * 0.0249 * 0.25 ≈ 62.4
    # Floor = 1000 * 0.50 = 500. Cap < floor → DENY.
    d = g.check(
        make_order(strategy="s1", notional=1000),
        GuardContext(capital_usd=10_000),
    )
    assert d.result == GuardResult.DENY
    assert "below" in d.reason


def test_g10_by_symbol_keys_on_symbol():
    """When by_symbol=True, the guard queries provider with symbol= not
    strategy_id="""
    seen_kwargs: dict = {}
    class CapturingProvider:
        def stats(self, **kw):
            seen_kwargs.update(kw)
            return stats(n=100, win=0.6, win_pct=0.02, loss_pct=0.01)
    g = KellyPositionGuard(
        win_rate_provider=CapturingProvider(), by_symbol=True,
    )
    g.check(
        make_order(strategy="s1", symbol="crypto:OKX:ETH/USDT:USDT", notional=500),
        GuardContext(capital_usd=10_000),
    )
    assert seen_kwargs["strategy_id"] is None
    assert seen_kwargs["symbol"] == "crypto:OKX:ETH/USDT:USDT"


def test_g10_lookback_days_passed_through():
    seen_kwargs: dict = {}
    class CapturingProvider:
        def stats(self, **kw):
            seen_kwargs.update(kw)
            return None
    g = KellyPositionGuard(
        win_rate_provider=CapturingProvider(), lookback_days=90,
    )
    g.check(make_order(), GuardContext(capital_usd=10_000))
    assert seen_kwargs["lookback_days"] == 90


# ================================================================== #
# Pipeline integration
# ================================================================== #
def test_g10_in_pipeline_after_per_strategy():
    """Realistic chain: PerStrategy SCALEs to 2000, G10 SCALEs further to 1000."""
    from risk import PerStrategyExposureGuard
    p = InMemoryWinRateProvider()
    p.add(stats(n=100, win=0.6, win_pct=0.02, loss_pct=0.01), strategy_id="s1")

    pipeline = GuardPipeline([
        PerStrategyExposureGuard(cap_pct_of_capital=0.30),     # 3000 cap
        KellyPositionGuard(win_rate_provider=p, safety_factor=0.25),  # 1000 cap
    ])
    ctx = GuardContext(
        capital_usd=10_000,
        open_notional_by_strategy={"s1": 1000},   # room 2000
    )
    run = pipeline.evaluate(make_order(strategy="s1", notional=3000), ctx)
    # PerStrategy scales 3000 → 2000
    # G10 scales 2000 → 1000 (Kelly cap)
    assert run.accepted
    assert run.final_notional_usd == pytest.approx(1000.0)
