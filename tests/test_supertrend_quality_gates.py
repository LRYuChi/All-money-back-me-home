"""Tests for R91 candidates — env overrides for the 3 next-biggest entry blockers.

Mirrors the R89 SUPERTREND_VOL_MULT pattern (see test_supertrend_vol_mult.py).
Covers:
  SUPERTREND_QUALITY_MIN       — relax trend_quality > 0.5 default
  SUPERTREND_ADX_MIN           — relax adx > 25 default
  SUPERTREND_REQUIRE_ATR_RISING — disable atr_rising gate (set to "0")
"""
from __future__ import annotations

import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


def _baseline_dataframe(
    *,
    vol_ratio: float = 1.5,
    trend_quality: float = 0.8,
    adx: float = 30.0,
    atr_rising: bool = True,
) -> pd.DataFrame:
    """Baseline where confirmed_long fires UNLESS one knob is intentionally weakened."""
    n = 30
    rows = []
    for i in range(n):
        is_last = (i == n - 1)
        rows.append({
            "close": 100.0 + i * 0.1,
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "volume": 100.0 * vol_ratio,
            "volume_ma_20": 100.0,
            "atr_rising": atr_rising,
            "trend_quality": trend_quality,
            "adx": adx,
            "st_buy": is_last,
            "st_sell": False,
            "all_bullish": True,
            "all_bearish": False,
            "fr_ok_long": True,
            "fr_ok_short": True,
            "st_trend": 1,
            "st_1h": 1,
            "pair_bullish_2tf": True,
            "pair_bearish_2tf": False,
        })
    return pd.DataFrame(rows)


@pytest.fixture
def strategy():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.adx_threshold = 25.0
    return s


def _last_tag(out: pd.DataFrame) -> str | None:
    return out.get("enter_tag", pd.Series([None] * len(out))).iloc[-1]


# =================================================================== #
# SUPERTREND_QUALITY_MIN
# =================================================================== #
def test_default_blocks_at_quality_below_threshold(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_QUALITY_MIN", raising=False)
    df = _baseline_dataframe(trend_quality=0.45)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"


def test_quality_min_lowered_allows_marginal_quality(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_QUALITY_MIN", "0.4")
    df = _baseline_dataframe(trend_quality=0.45)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) == "confirmed"


def test_quality_min_raised_blocks_normal_quality(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_QUALITY_MIN", "0.9")
    df = _baseline_dataframe(trend_quality=0.8)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"


def test_quality_min_invalid_falls_back_to_default(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_QUALITY_MIN", "garbage")
    df = _baseline_dataframe(trend_quality=0.8)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) == "confirmed"


# =================================================================== #
# SUPERTREND_ADX_MIN
# =================================================================== #
def test_default_blocks_at_adx_below_threshold(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_ADX_MIN", raising=False)
    df = _baseline_dataframe(adx=22.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"


def test_adx_min_lowered_allows_weaker_trend(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ADX_MIN", "20")
    df = _baseline_dataframe(adx=22.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) == "confirmed"


def test_adx_min_raised_blocks_normal_trend(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ADX_MIN", "40")
    df = _baseline_dataframe(adx=30.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"


def test_adx_min_invalid_falls_back_to_attribute_default(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ADX_MIN", "not-a-number")
    df = _baseline_dataframe(adx=30.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) == "confirmed"


# =================================================================== #
# SUPERTREND_REQUIRE_ATR_RISING
# =================================================================== #
def test_default_blocks_when_atr_not_rising(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_REQUIRE_ATR_RISING", raising=False)
    df = _baseline_dataframe(atr_rising=False)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"


def test_atr_rising_disabled_allows_flat_atr(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_REQUIRE_ATR_RISING", "0")
    df = _baseline_dataframe(atr_rising=False)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) == "confirmed"


def test_atr_rising_explicit_one_keeps_default_behavior(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_REQUIRE_ATR_RISING", "1")
    df = _baseline_dataframe(atr_rising=False)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert _last_tag(out) != "confirmed"
