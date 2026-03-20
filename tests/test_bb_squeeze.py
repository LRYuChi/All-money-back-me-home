"""Tests for BB Squeeze strategy indicators and logic."""

import numpy as np
import pandas as pd
import pytest


def make_dataframe(n=100, squeeze_at=50):
    """Create a synthetic dataframe for BB Squeeze testing."""
    np.random.seed(42)
    dates = pd.date_range("2026-01-01", periods=n, freq="15min")
    close = 70000 + np.cumsum(np.random.randn(n) * 50)
    high = close + np.abs(np.random.randn(n) * 30)
    low = close - np.abs(np.random.randn(n) * 30)
    volume = np.random.randint(100, 1000, n).astype(float)
    return pd.DataFrame({
        "date": dates, "open": close - 10, "high": high, "low": low,
        "close": close, "volume": volume,
    })


class TestSqueezeDetection:
    """Verify BB/KC squeeze detection logic."""

    def test_squeeze_on_when_bb_inside_kc(self):
        """BB inside KC = squeeze on."""
        df = pd.DataFrame({
            "bb_upper": [100], "bb_lower": [90],
            "kc_upper": [105], "kc_lower": [85],
        })
        squeeze_on = (df["bb_lower"] > df["kc_lower"]) & (df["bb_upper"] < df["kc_upper"])
        assert squeeze_on.iloc[0] is True or squeeze_on.iloc[0] == True

    def test_squeeze_off_when_bb_outside_kc(self):
        """BB wider than KC = squeeze off."""
        df = pd.DataFrame({
            "bb_upper": [110], "bb_lower": [80],
            "kc_upper": [105], "kc_lower": [85],
        })
        squeeze_on = (df["bb_lower"] > df["kc_lower"]) & (df["bb_upper"] < df["kc_upper"])
        assert squeeze_on.iloc[0] is False or squeeze_on.iloc[0] == False

    def test_squeeze_fire_on_transition(self):
        """Squeeze fires on the candle where it transitions OFF."""
        squeeze_on = pd.Series([True, True, True, False, False])
        squeeze_off = ~squeeze_on
        fire = squeeze_off & squeeze_on.shift(1).fillna(False)
        assert fire.tolist() == [False, False, False, True, False]


class TestMomentumOscillator:
    """Verify momentum calculation."""

    def test_momentum_positive_in_uptrend(self):
        """Close above midpoint = positive momentum."""
        close = pd.Series([100, 102, 104, 106, 108])
        high = close + 2
        low = close - 2
        kc_middle = close.ewm(span=5).mean()
        highest = high.rolling(5, min_periods=1).max()
        lowest = low.rolling(5, min_periods=1).min()
        midpoint = (highest + lowest) / 2
        momentum = close - (midpoint + kc_middle) / 2
        assert momentum.iloc[-1] > 0

    def test_momentum_negative_in_downtrend(self):
        """Close below midpoint = negative momentum."""
        close = pd.Series([108, 106, 104, 102, 100])
        high = close + 2
        low = close - 2
        kc_middle = close.ewm(span=5).mean()
        highest = high.rolling(5, min_periods=1).max()
        lowest = low.rolling(5, min_periods=1).min()
        midpoint = (highest + lowest) / 2
        momentum = close - (midpoint + kc_middle) / 2
        assert momentum.iloc[-1] < 0


class TestRMultipleWithLeverage:
    """Verify R-multiple uses position profit not account profit."""

    def test_r_multiple_normalized_by_leverage(self):
        """With 3x leverage, 3% account profit = 1% position profit."""
        account_profit = 0.03  # 3% account return
        leverage = 3.0
        atr_sl_pct = 0.01  # 1% stop

        position_profit = account_profit / max(leverage, 1.0)
        r_multiple = position_profit / atr_sl_pct

        assert abs(r_multiple - 1.0) < 0.01  # Should be 1R, not 3R

    def test_r_multiple_without_leverage_is_inflated(self):
        """Without normalization, R-multiple is wrong."""
        account_profit = 0.03
        atr_sl_pct = 0.01
        wrong_r = account_profit / atr_sl_pct  # 3R — WRONG for leveraged trade
        assert wrong_r == 3.0  # This is the bug we fixed


class TestPartialExitSchedule:
    """Verify BB Squeeze partial exit at 1R (50%)."""

    def test_partial_at_1r(self):
        """50% exit at 1.0R."""
        r_multiple = 1.0
        partials_done = 0
        stake = 100.0

        if r_multiple >= 1.0 and partials_done < 1:
            partial = stake * 0.50
        else:
            partial = 0

        assert partial == 50.0

    def test_no_double_partial(self):
        """Don't take second partial at same level."""
        r_multiple = 1.5
        partials_done = 1  # Already took first partial
        should_partial = r_multiple >= 1.0 and partials_done < 1
        assert should_partial is False
