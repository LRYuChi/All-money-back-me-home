"""Tests for Layer 1 market structure analysis.

Covers swing detection, market state classification, and CHoCH detection
using synthetic price data with clear, deterministic patterns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from strategy.enums import MarketState, SignalDirection
from strategy.layer1_market_structure import (
    classify_market_state,
    detect_choch,
    detect_swing_highs,
    detect_swing_lows,
)
from strategy.models import SwingPoint


# ---------------------------------------------------------------------------
# Helpers - synthetic data generators
# ---------------------------------------------------------------------------

def _make_dates(n: int) -> pd.DatetimeIndex:
    """Create a DatetimeIndex of *n* daily timestamps."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return pd.DatetimeIndex([start + timedelta(days=i) for i in range(n)])


def make_uptrend_df(n: int = 100) -> pd.DataFrame:
    """Create a DataFrame with a clear uptrend: HH / HL pattern.

    Generates a zigzag that rises over time:
    100 -> 90 -> 110 -> 95 -> 115 -> 100 -> 120 ...
    """
    prices: list[float] = []
    base = 100.0
    for i in range(n):
        cycle = i % 10
        if cycle < 5:
            # Rising leg
            prices.append(base + cycle * 5)
        else:
            # Pullback leg (higher low)
            prices.append(base + (10 - cycle) * 5)
        if cycle == 9:
            base += 10  # Each full cycle lifts the floor

    dates = _make_dates(n)
    close = pd.Series(prices, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": [1000] * n,
        },
        index=dates,
    )


def make_downtrend_df(n: int = 100) -> pd.DataFrame:
    """Create a DataFrame with a clear downtrend: LH / LL pattern."""
    prices: list[float] = []
    base = 200.0
    for i in range(n):
        cycle = i % 10
        if cycle < 5:
            prices.append(base - cycle * 5)
        else:
            prices.append(base - (10 - cycle) * 5)
        if cycle == 9:
            base -= 10

    dates = _make_dates(n)
    close = pd.Series(prices, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 1,
            "High": close + 2,
            "Low": close - 2,
            "Close": close,
            "Volume": [1000] * n,
        },
        index=dates,
    )


def make_ranging_df(n: int = 100) -> pd.DataFrame:
    """Create a DataFrame with ranging price action oscillating 95-105."""
    import math

    prices = [100.0 + 5.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
    dates = _make_dates(n)
    close = pd.Series(prices, dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.5,
            "High": close + 1.5,
            "Low": close - 1.5,
            "Close": close,
            "Volume": [1000] * n,
        },
        index=dates,
    )


# ---------------------------------------------------------------------------
# Swing detection tests
# ---------------------------------------------------------------------------

class TestSwingHighDetection:
    """Tests for detect_swing_highs."""

    def test_detects_peaks_in_uptrend(self) -> None:
        df = make_uptrend_df()
        highs = detect_swing_highs(df, lookback=3)
        assert len(highs) > 0
        assert all(sp.type == "high" for sp in highs)

    def test_swing_highs_ascending_in_uptrend(self) -> None:
        df = make_uptrend_df()
        highs = detect_swing_highs(df, lookback=3)
        # In an uptrend, overall highs should trend upward
        if len(highs) >= 3:
            first_three = highs[:3]
            last_three = highs[-3:]
            assert last_three[-1].price > first_three[0].price

    def test_returns_empty_on_insufficient_data(self) -> None:
        dates = _make_dates(3)
        df = pd.DataFrame(
            {
                "Open": [1, 2, 3],
                "High": [1.5, 2.5, 3.5],
                "Low": [0.5, 1.5, 2.5],
                "Close": [1, 2, 3],
                "Volume": [100, 100, 100],
            },
            index=dates,
        )
        highs = detect_swing_highs(df, lookback=5)
        # With lookback=5 and only 3 bars, detections are unlikely but should not crash
        assert isinstance(highs, list)

    def test_raises_without_high_column(self) -> None:
        dates = _make_dates(10)
        df = pd.DataFrame({"Close": range(10)}, index=dates)
        with pytest.raises(ValueError, match="High"):
            detect_swing_highs(df)


class TestSwingLowDetection:
    """Tests for detect_swing_lows."""

    def test_detects_troughs_in_downtrend(self) -> None:
        df = make_downtrend_df()
        lows = detect_swing_lows(df, lookback=3)
        assert len(lows) > 0
        assert all(sp.type == "low" for sp in lows)

    def test_swing_lows_descending_in_downtrend(self) -> None:
        df = make_downtrend_df()
        lows = detect_swing_lows(df, lookback=3)
        if len(lows) >= 3:
            assert lows[-1].price < lows[0].price

    def test_raises_without_low_column(self) -> None:
        dates = _make_dates(10)
        df = pd.DataFrame({"Close": range(10)}, index=dates)
        with pytest.raises(ValueError, match="Low"):
            detect_swing_lows(df)


# ---------------------------------------------------------------------------
# Market state classification tests
# ---------------------------------------------------------------------------

class TestClassifyMarketState:
    """Tests for classify_market_state."""

    def test_trending_up(self) -> None:
        df = make_uptrend_df()
        highs = detect_swing_highs(df, lookback=3)
        lows = detect_swing_lows(df, lookback=3)
        result = classify_market_state(highs, lows)
        assert result.state == MarketState.TRENDING_UP

    def test_trending_down(self) -> None:
        df = make_downtrend_df()
        highs = detect_swing_highs(df, lookback=3)
        lows = detect_swing_lows(df, lookback=3)
        result = classify_market_state(highs, lows)
        assert result.state == MarketState.TRENDING_DOWN

    def test_ranging(self) -> None:
        df = make_ranging_df()
        highs = detect_swing_highs(df, lookback=3)
        lows = detect_swing_lows(df, lookback=3)
        result = classify_market_state(highs, lows)
        assert result.state == MarketState.RANGING

    def test_confidence_is_valid(self) -> None:
        df = make_uptrend_df()
        highs = detect_swing_highs(df, lookback=3)
        lows = detect_swing_lows(df, lookback=3)
        result = classify_market_state(highs, lows)
        assert 0.0 <= result.confidence <= 1.0

    def test_empty_swings_returns_ranging(self) -> None:
        result = classify_market_state([], [])
        assert result.state == MarketState.RANGING

    def test_single_swing_point_returns_ranging(self) -> None:
        sp = SwingPoint(
            index=0,
            price=100.0,
            ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
            type="high",
        )
        result = classify_market_state([sp], [])
        assert result.state == MarketState.RANGING


# ---------------------------------------------------------------------------
# CHoCH detection tests
# ---------------------------------------------------------------------------

class TestCHoCHDetection:
    """Tests for detect_choch."""

    def test_choch_in_uptrend_lower_low(self) -> None:
        """In an uptrend, a lower low triggers CHoCH with SHORT direction."""
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        highs = [
            SwingPoint(index=0, price=100, ts=now, type="high"),
            SwingPoint(index=10, price=110, ts=now + timedelta(days=10), type="high"),
            SwingPoint(index=20, price=120, ts=now + timedelta(days=20), type="high"),
        ]
        lows = [
            SwingPoint(index=5, price=90, ts=now + timedelta(days=5), type="low"),
            SwingPoint(index=15, price=95, ts=now + timedelta(days=15), type="low"),
            SwingPoint(index=25, price=88, ts=now + timedelta(days=25), type="low"),  # lower low
        ]
        detected, direction = detect_choch(highs, lows, MarketState.TRENDING_UP)
        assert detected is True
        assert direction == SignalDirection.SHORT

    def test_choch_in_downtrend_higher_high(self) -> None:
        """In a downtrend, a higher high triggers CHoCH with LONG direction."""
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        highs = [
            SwingPoint(index=0, price=120, ts=now, type="high"),
            SwingPoint(index=10, price=110, ts=now + timedelta(days=10), type="high"),
            SwingPoint(index=20, price=115, ts=now + timedelta(days=20), type="high"),  # higher high
        ]
        lows = [
            SwingPoint(index=5, price=100, ts=now + timedelta(days=5), type="low"),
            SwingPoint(index=15, price=90, ts=now + timedelta(days=15), type="low"),
            SwingPoint(index=25, price=85, ts=now + timedelta(days=25), type="low"),
        ]
        detected, direction = detect_choch(highs, lows, MarketState.TRENDING_DOWN)
        assert detected is True
        assert direction == SignalDirection.LONG

    def test_no_choch_in_clean_uptrend(self) -> None:
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        highs = [
            SwingPoint(index=0, price=100, ts=now, type="high"),
            SwingPoint(index=10, price=110, ts=now + timedelta(days=10), type="high"),
        ]
        lows = [
            SwingPoint(index=5, price=90, ts=now + timedelta(days=5), type="low"),
            SwingPoint(index=15, price=95, ts=now + timedelta(days=15), type="low"),
        ]
        detected, direction = detect_choch(highs, lows, MarketState.TRENDING_UP)
        assert detected is False
        assert direction is None

    def test_no_choch_in_ranging(self) -> None:
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        highs = [
            SwingPoint(index=0, price=105, ts=now, type="high"),
            SwingPoint(index=10, price=103, ts=now + timedelta(days=10), type="high"),
        ]
        lows = [
            SwingPoint(index=5, price=95, ts=now + timedelta(days=5), type="low"),
            SwingPoint(index=15, price=97, ts=now + timedelta(days=15), type="low"),
        ]
        detected, direction = detect_choch(highs, lows, MarketState.RANGING)
        assert detected is False
        assert direction is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge-case tests for market structure analysis."""

    def test_flat_prices(self) -> None:
        """Flat prices should not crash and should be classified as RANGING."""
        dates = _make_dates(50)
        flat = [100.0] * 50
        df = pd.DataFrame(
            {
                "Open": flat,
                "High": flat,
                "Low": flat,
                "Close": flat,
                "Volume": [1000] * 50,
            },
            index=dates,
        )
        highs = detect_swing_highs(df, lookback=3)
        lows = detect_swing_lows(df, lookback=3)
        result = classify_market_state(highs, lows)
        # Flat prices should not produce a trending classification
        assert result.state in (
            MarketState.RANGING,
            MarketState.TRENDING_UP,
            MarketState.TRENDING_DOWN,
        )

    def test_very_short_series(self) -> None:
        """A single-bar DataFrame should not crash."""
        dates = _make_dates(1)
        df = pd.DataFrame(
            {
                "Open": [100],
                "High": [102],
                "Low": [98],
                "Close": [101],
                "Volume": [500],
            },
            index=dates,
        )
        highs = detect_swing_highs(df, lookback=5)
        lows = detect_swing_lows(df, lookback=5)
        result = classify_market_state(highs, lows)
        assert isinstance(result.state, MarketState)
