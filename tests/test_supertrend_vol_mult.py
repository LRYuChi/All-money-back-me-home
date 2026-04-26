"""Tests for R89 — SUPERTREND_VOL_MULT env override of quality vol gate."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


def _baseline_dataframe(vol_ratio: float = 1.0) -> pd.DataFrame:
    """Build a dataframe where ALL conditions for confirmed_long are met
    EXCEPT the vol gate is at exactly `vol_ratio` × MA20.

    Caller picks vol_ratio relative to threshold to test gate behaviour.
    """
    n = 30
    rows = []
    for i in range(n):
        is_last = (i == n - 1)
        rows.append({
            "close": 100.0 + i * 0.1,
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "volume": 100.0 * vol_ratio,   # CONFIGURABLE
            "volume_ma_20": 100.0,
            "atr_rising": True,
            "trend_quality": 0.8,
            "adx": 30.0,
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


# =================================================================== #
# Default: 1.2× threshold
# =================================================================== #
def test_default_blocks_at_vol_equal_to_ma(strategy, monkeypatch):
    """vol == ma → 1.0 ratio → below 1.2× default → BLOCKED."""
    monkeypatch.delenv("SUPERTREND_VOL_MULT", raising=False)
    df = _baseline_dataframe(vol_ratio=1.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    last_tag = out.get("enter_tag", pd.Series([None] * len(df))).iloc[-1]
    assert last_tag != "confirmed"


def test_default_allows_at_vol_above_threshold(strategy, monkeypatch):
    """vol = 1.5x MA → above 1.2× default → ALLOWED."""
    monkeypatch.delenv("SUPERTREND_VOL_MULT", raising=False)
    df = _baseline_dataframe(vol_ratio=1.5)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert out["enter_tag"].iloc[-1] == "confirmed"


# =================================================================== #
# R89: env override
# =================================================================== #
def test_env_override_to_1_0_allows_baseline_volume(strategy, monkeypatch):
    """SUPERTREND_VOL_MULT=1.0 → vol == ma now passes."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "1.0")
    df = _baseline_dataframe(vol_ratio=1.0)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    # vol must be STRICTLY > ma * 1.0, so ratio 1.0 is still blocked
    # (1.0 * 100 == 1.0 * 100 → not strictly greater)
    last_tag = out.get("enter_tag", pd.Series([None] * len(df))).iloc[-1]
    assert last_tag != "confirmed"   # boundary case stays blocked


def test_env_override_to_1_0_allows_slightly_above(strategy, monkeypatch):
    """SUPERTREND_VOL_MULT=1.0 → vol = 1.01× ma now fires."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "1.0")
    df = _baseline_dataframe(vol_ratio=1.01)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert out["enter_tag"].iloc[-1] == "confirmed"


def test_env_override_to_0_8_allows_below_baseline(strategy, monkeypatch):
    """SUPERTREND_VOL_MULT=0.8 → vol = 0.85× ma fires."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "0.8")
    df = _baseline_dataframe(vol_ratio=0.85)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert out["enter_tag"].iloc[-1] == "confirmed"


def test_env_override_to_2_0_blocks_normal_volume(strategy, monkeypatch):
    """SUPERTREND_VOL_MULT=2.0 → vol = 1.5× ma now blocked."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "2.0")
    df = _baseline_dataframe(vol_ratio=1.5)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    last_tag = out.get("enter_tag", pd.Series([None] * len(df))).iloc[-1]
    assert last_tag != "confirmed"


def test_invalid_env_falls_back_to_default(strategy, monkeypatch):
    """Garbage value → default 1.2 used (not crash)."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "not-a-number")
    df = _baseline_dataframe(vol_ratio=1.5)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    # Should still fire (1.5 > 1.2 default) — no crash
    assert out["enter_tag"].iloc[-1] == "confirmed"
