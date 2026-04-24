"""Tests for fusion.context_provider — Static + Cached + HLBTC."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from fusion.context_provider import (
    CachedContextProvider,
    HLBTCContextProvider,
    StaticContextProvider,
)
from fusion.regime import MarketContext


# ================================================================== #
# StaticContextProvider
# ================================================================== #
def test_static_returns_same_context_each_call():
    ctx = MarketContext(btc_price=50_000, btc_ma200=48_000, btc_ma200_slope=0.001,
                        btc_realized_vol=0.5, vix=15)
    p = StaticContextProvider(ctx)
    a = p.get()
    b = p.get()
    assert a is b


# ================================================================== #
# CachedContextProvider
# ================================================================== #
class CountingProvider:
    def __init__(self, ctx):
        self.calls = 0
        self._ctx = ctx
    def get(self):
        self.calls += 1
        return self._ctx


def test_cache_returns_first_result():
    ctx = MarketContext(btc_price=50_000)
    upstream = CountingProvider(ctx)
    cached = CachedContextProvider(upstream, ttl_seconds=300, now=lambda: 1000.0)
    assert cached.get().btc_price == 50_000


def test_cache_hits_within_ttl():
    upstream = CountingProvider(MarketContext(btc_price=50_000))
    t = [1000.0]
    cached = CachedContextProvider(upstream, ttl_seconds=60, now=lambda: t[0])
    cached.get()       # miss
    t[0] = 1030.0      # +30s
    cached.get()       # hit
    cached.get()       # hit
    assert upstream.calls == 1


def test_cache_expires_at_ttl_boundary():
    upstream = CountingProvider(MarketContext(btc_price=50_000))
    t = [1000.0]
    cached = CachedContextProvider(upstream, ttl_seconds=60, now=lambda: t[0])
    cached.get()       # miss → calls=1
    t[0] = 1060.0      # +60s exactly
    cached.get()       # boundary: re-fetch
    assert upstream.calls == 2


def test_cache_picks_up_upstream_changes_after_ttl():
    """If upstream returns a different context, cache reflects it after TTL."""
    state = {"v": MarketContext(btc_price=50_000)}

    class MutatingProvider:
        def __init__(self):
            self.calls = 0
        def get(self):
            self.calls += 1
            return state["v"]

    upstream = MutatingProvider()
    t = [1000.0]
    cached = CachedContextProvider(upstream, ttl_seconds=60, now=lambda: t[0])

    assert cached.get().btc_price == 50_000
    state["v"] = MarketContext(btc_price=55_000)
    assert cached.get().btc_price == 50_000  # still cached
    t[0] = 1100.0
    assert cached.get().btc_price == 55_000


# ================================================================== #
# HLBTCContextProvider
# ================================================================== #
class FakeHLInfo:
    """Returns canned daily candles."""

    def __init__(self, candles=None, *, raises=None):
        self.candles = candles or []
        self.raises = raises
        self.calls = []

    def candles_snapshot(self, name, interval, startTime, endTime):
        self.calls.append({"name": name, "interval": interval,
                           "startTime": startTime, "endTime": endTime})
        if self.raises:
            raise self.raises
        return self.candles


def daily_candle(start_dt: datetime, close: float) -> dict:
    return {
        "t": int(start_dt.timestamp() * 1000),
        "T": int((start_dt + timedelta(days=1)).timestamp() * 1000),
        "o": str(close), "h": str(close), "l": str(close),
        "c": str(close), "v": "100", "i": "1d", "s": "BTC", "n": 100,
    }


def test_hl_basic_extraction():
    """200+ candles with constant price — MA200 = price, slope = 0, vol ≈ 0."""
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base_ts + timedelta(days=i), 50_000.0) for i in range(210)]
    info = FakeHLInfo(candles)

    p = HLBTCContextProvider(info, now=lambda: base_ts + timedelta(days=210))
    ctx = p.get()

    assert ctx.btc_price == 50_000.0
    assert ctx.btc_ma200 == pytest.approx(50_000.0)
    assert ctx.btc_ma200_slope == pytest.approx(0.0, abs=1e-12)
    # Constant price → vol = 0
    assert ctx.btc_realized_vol == pytest.approx(0.0, abs=1e-12)
    assert ctx.daily_dd_pct == 0.0


def test_hl_uptrend_produces_positive_slope():
    """Linearly rising BTC: 50k → 60k over 210 days."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(210):
        px = 50_000 + (10_000 * i / 209)
        candles.append(daily_candle(base + timedelta(days=i), px))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()

    assert ctx.btc_price == pytest.approx(60_000.0)
    # MA200 should be < current price (price is rising past it)
    assert ctx.btc_ma200 < ctx.btc_price
    # Slope should be positive
    assert ctx.btc_ma200_slope > 0


def test_hl_downtrend_produces_negative_slope():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(210):
        px = 60_000 - (10_000 * i / 209)
        candles.append(daily_candle(base + timedelta(days=i), px))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()

    assert ctx.btc_ma200_slope < 0


def test_hl_volatile_market_high_realized_vol():
    """Alternating 50k/55k each day → ~10% daily moves → very high annualized vol."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(210):
        px = 55_000 if i % 2 == 0 else 50_000
        candles.append(daily_candle(base + timedelta(days=i), px))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()

    # ~10% daily moves alternating → annualized vol > 1.0
    assert ctx.btc_realized_vol > 1.0


def test_hl_today_drop_produces_dd():
    """Yesterday 50k, today 47.5k → 5% drawdown."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0) for i in range(209)]
    candles.append(daily_candle(base + timedelta(days=209), 47_500.0))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()

    assert ctx.daily_dd_pct == pytest.approx(0.05)


def test_hl_no_dd_when_today_higher():
    """Today's close ≥ yesterday's → no drawdown (returns 0, not negative)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0) for i in range(209)]
    candles.append(daily_candle(base + timedelta(days=209), 52_000.0))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()

    assert ctx.daily_dd_pct == 0.0


def test_hl_insufficient_candles_returns_empty_context():
    info = FakeHLInfo([])
    p = HLBTCContextProvider(info)
    ctx = p.get()
    assert ctx.btc_price is None
    assert ctx.btc_ma200 is None


def test_hl_api_failure_returns_empty_context():
    info = FakeHLInfo(raises=ConnectionError("network"))
    p = HLBTCContextProvider(info)
    ctx = p.get()
    assert ctx.btc_price is None


def test_hl_partial_window_uses_what_it_has():
    """Only 50 candles available → MA50, no slope yet, no vol."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0 + i * 100) for i in range(50)]
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=50))
    ctx = p.get()

    assert ctx.btc_price == 50_000.0 + 49 * 100
    assert ctx.btc_ma200 is not None  # uses 50-day mean
    # Slope needs 200+5 = 205 candles, we only have 50 → None
    assert ctx.btc_ma200_slope is None
    # Vol needs 60+1, we have 50 → None
    assert ctx.btc_realized_vol is None


# ================================================================== #
# VIX provider integration
# ================================================================== #
def test_hl_with_vix_provider():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0) for i in range(210)]
    info = FakeHLInfo(candles)

    p = HLBTCContextProvider(
        info,
        vix_provider=lambda: 18.5,
        now=lambda: base + timedelta(days=210),
    )
    ctx = p.get()
    assert ctx.vix == 18.5


def test_hl_vix_provider_failure_returns_none_vix():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0) for i in range(210)]
    info = FakeHLInfo(candles)

    def bad_vix():
        raise RuntimeError("yfinance down")

    p = HLBTCContextProvider(
        info, vix_provider=bad_vix,
        now=lambda: base + timedelta(days=210),
    )
    ctx = p.get()
    # Other fields still present, just VIX missing
    assert ctx.vix is None
    assert ctx.btc_price == 50_000.0


def test_hl_no_vix_provider_returns_none():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = [daily_candle(base + timedelta(days=i), 50_000.0) for i in range(210)]
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()
    assert ctx.vix is None


# ================================================================== #
# Detector integration: HL provider output drives sensible regime
# ================================================================== #
def test_hl_provider_uptrend_yields_bull_regime():
    from fusion import detect_regime, Regime

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    for i in range(210):
        # Steady uptrend with low daily vol
        px = 50_000 * (1.001 ** i)  # ~22% over 210 days, low daily moves
        candles.append(daily_candle(base + timedelta(days=i), px))
    info = FakeHLInfo(candles)
    p = HLBTCContextProvider(info, now=lambda: base + timedelta(days=210))
    ctx = p.get()
    regime = detect_regime(ctx)
    # Price > MA, slope > 0, vol low → BULL_TRENDING
    assert regime == Regime.BULL_TRENDING
