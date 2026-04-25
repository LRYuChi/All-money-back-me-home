"""Tests for ExposureProvider + make_context_provider (round 21)."""
from __future__ import annotations

import pytest

from execution.pending_orders.types import PendingOrder
from risk import (
    GuardContext,
    InMemoryExposureProvider,
    NoOpExposureProvider,
    build_exposure_provider,
    make_context_provider,
)
from risk.exposure_provider import _market_from_symbol, _row_notional


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(strategy="s1", symbol="crypto:OKX:BTC/USDT:USDT") -> PendingOrder:
    return PendingOrder(
        strategy_id=strategy, symbol=symbol, side="long",
        target_notional_usd=100.0, mode="shadow",
    )


# ================================================================== #
# Helpers under test
# ================================================================== #
def test_market_from_symbol_canonical():
    assert _market_from_symbol("crypto:OKX:BTC/USDT:USDT") == "crypto"
    assert _market_from_symbol("us:NASDAQ:AAPL") == "us"
    assert _market_from_symbol("tw:TPE:2330") == "tw"
    assert _market_from_symbol("FX:OANDA:EURUSD") == "fx"  # case-normalized
    assert _market_from_symbol("badsymbol") == "unknown"
    assert _market_from_symbol("") == "unknown"


def test_row_notional_basic():
    assert _row_notional({"size": 0.5, "entry_price": 50_000}) == 25_000.0


def test_row_notional_takes_absolute_value():
    """Short positions have negative size; we want absolute notional."""
    assert _row_notional({"size": -0.5, "entry_price": 50_000}) == 25_000.0


def test_row_notional_returns_none_on_missing():
    assert _row_notional({"size": None, "entry_price": 100}) is None
    assert _row_notional({"size": 1, "entry_price": None}) is None
    assert _row_notional({}) is None


def test_row_notional_returns_none_on_zero():
    assert _row_notional({"size": 0, "entry_price": 100}) is None
    assert _row_notional({"size": 1, "entry_price": 0}) is None


def test_row_notional_returns_none_on_unparseable():
    assert _row_notional({"size": "abc", "entry_price": 100}) is None


# ================================================================== #
# NoOpExposureProvider
# ================================================================== #
def test_noop_returns_zero_everywhere():
    p = NoOpExposureProvider()
    assert p.open_by_strategy() == {}
    assert p.open_by_market() == {}
    assert p.global_open() == 0.0


# ================================================================== #
# InMemoryExposureProvider
# ================================================================== #
def test_inmemory_aggregates_by_strategy():
    p = InMemoryExposureProvider([
        {"strategy_id": "alpha", "symbol": "crypto:X", "notional_usd": 100},
        {"strategy_id": "alpha", "symbol": "crypto:X", "notional_usd": 200},
        {"strategy_id": "beta", "symbol": "us:Y", "notional_usd": 50},
    ])
    out = p.open_by_strategy()
    assert out == {"alpha": 300.0, "beta": 50.0}


def test_inmemory_aggregates_by_market():
    p = InMemoryExposureProvider([
        {"strategy_id": "s1", "symbol": "crypto:OKX:BTC/USDT:USDT", "notional_usd": 100},
        {"strategy_id": "s2", "symbol": "crypto:OKX:ETH/USDT:USDT", "notional_usd": 200},
        {"strategy_id": "s3", "symbol": "us:NASDAQ:AAPL", "notional_usd": 500},
    ])
    out = p.open_by_market()
    assert out == {"crypto": 300.0, "us": 500.0}


def test_inmemory_global_open():
    p = InMemoryExposureProvider([
        {"strategy_id": "a", "symbol": "crypto:x", "notional_usd": 100},
        {"strategy_id": "b", "symbol": "us:y", "notional_usd": 50},
    ])
    assert p.global_open() == 150.0


def test_inmemory_negative_notional_treated_as_absolute():
    """Defensive: short positions reported with negative notional should
    still count toward exposure, not subtract from it."""
    p = InMemoryExposureProvider()
    p.add(strategy_id="s", symbol="crypto:x", notional_usd=-100)
    assert p.global_open() == 100.0


# ================================================================== #
# make_context_provider
# ================================================================== #
def test_context_provider_builds_guardcontext():
    exp = InMemoryExposureProvider([
        {"strategy_id": "alpha", "symbol": "crypto:OKX:BTC/USDT:USDT",
         "notional_usd": 1500.0},
    ])
    provide = make_context_provider(capital_usd=10_000, exposure=exp)

    ctx = provide(make_order(strategy="alpha"))
    assert isinstance(ctx, GuardContext)
    assert ctx.capital_usd == 10_000
    assert ctx.open_notional_by_strategy == {"alpha": 1500.0}
    assert ctx.open_notional_by_market == {"crypto": 1500.0}
    assert ctx.global_open_notional == 1500.0
    assert ctx.signal_age_seconds is None  # no provider given


def test_context_provider_with_signal_age_provider():
    exp = NoOpExposureProvider()
    provide = make_context_provider(
        capital_usd=10_000, exposure=exp,
        signal_age_provider=lambda order: 5.5,
    )
    ctx = provide(make_order())
    assert ctx.signal_age_seconds == 5.5


def test_context_provider_swallows_signal_age_exception():
    """If signal_age_provider raises, fall back to None (G1 fails open)."""
    exp = NoOpExposureProvider()

    def bad_age(order):
        raise RuntimeError("DB blip")

    provide = make_context_provider(
        capital_usd=10_000, exposure=exp, signal_age_provider=bad_age,
    )
    ctx = provide(make_order())
    assert ctx.signal_age_seconds is None  # gracefully None


def test_context_provider_calls_refresh_on_each_call():
    """Cumulative effect of orders within a tick: each call refreshes
    the underlying exposure cache."""
    refresh_count = {"n": 0}

    class CountingExposure(NoOpExposureProvider):
        def refresh(self):
            refresh_count["n"] += 1

    provide = make_context_provider(
        capital_usd=10_000, exposure=CountingExposure(),
    )
    provide(make_order())
    provide(make_order())
    assert refresh_count["n"] == 2


def test_context_provider_swallows_refresh_exception():
    class BadRefreshExposure(NoOpExposureProvider):
        def refresh(self):
            raise RuntimeError("network")

    provide = make_context_provider(
        capital_usd=10_000, exposure=BadRefreshExposure(),
    )
    # Must not raise — just continues with stale data
    ctx = provide(make_order())
    assert ctx.global_open_notional == 0.0


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_noop_when_nothing_configured():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""
    p = build_exposure_provider(S())
    assert isinstance(p, NoOpExposureProvider)


def test_factory_postgres_when_dsn_set():
    from risk import PostgresExposureProvider
    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""
    p = build_exposure_provider(S())
    assert isinstance(p, PostgresExposureProvider)
