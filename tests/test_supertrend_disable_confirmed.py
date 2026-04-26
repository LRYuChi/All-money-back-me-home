"""Tests for R87 — SUPERTREND_DISABLE_CONFIRMED env gate.

Verifies the gate's behavior in populate_entry_trend without needing
full Freqtrade scaffolding. Uses synthetic dataframes that satisfy
confirmed-tier conditions; checks that the env flag flips the result.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


def _confirmed_long_dataframe(n: int = 30) -> pd.DataFrame:
    """Build a dataframe where the LAST row satisfies all confirmed_long
    conditions (st_buy + all_bullish + quality + fr_ok_long)."""
    rows = []
    for i in range(n):
        # Last row gets the firing conditions
        is_last = (i == n - 1)
        rows.append({
            "close": 100.0 + i * 0.1,
            "open": 100.0 + i * 0.1,
            "high": 100.5 + i * 0.1,
            "low": 99.5 + i * 0.1,
            "volume": 200.0,
            "volume_ma_20": 100.0,         # vol > 1.2 * 100 = 120 ✓
            "atr_rising": True,            # ✓
            "trend_quality": 0.8,          # > 0.5 ✓
            "adx": 30.0,                   # > 25 ✓
            "st_buy": is_last,             # only last row → confirmed fires
            "st_sell": False,
            "all_bullish": True,           # ✓
            "all_bearish": False,
            "fr_ok_long": True,            # ✓
            "fr_ok_short": True,
            "st_trend": 1,                 # 15m bullish
            "st_1h": 1,
            "pair_bullish_2tf": True,
            "pair_bearish_2tf": False,
        })
    df = pd.DataFrame(rows)
    return df


@pytest.fixture
def strategy():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.adx_threshold = 25.0
    return s


# =================================================================== #
# Default behavior (env unset) — confirmed tier fires
# =================================================================== #
def test_confirmed_fires_by_default(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_DISABLE_CONFIRMED", raising=False)
    df = _confirmed_long_dataframe()
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    # Last row should have enter_long=1 and enter_tag="confirmed"
    assert out["enter_long"].iloc[-1] == 1
    assert out["enter_tag"].iloc[-1] == "confirmed"


def test_confirmed_fires_when_env_explicitly_zero(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "0")
    df = _confirmed_long_dataframe()
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert out["enter_long"].iloc[-1] == 1
    assert out["enter_tag"].iloc[-1] == "confirmed"


# =================================================================== #
# R87: env=1 disables confirmed
# =================================================================== #
def test_confirmed_disabled_when_env_one(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    df = _confirmed_long_dataframe()
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    # Last row: confirmed mask was skipped — enter_long should NOT be set
    # by confirmed branch. (scout/pre_scout might still fire on different
    # conditions but our fixture doesn't satisfy their edge-trigger.)
    last_tag = out["enter_tag"].iloc[-1] if "enter_tag" in out.columns else None
    assert last_tag != "confirmed"


def test_disabled_short_side_also_skipped(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    # Build a confirmed-short scenario
    df = _confirmed_long_dataframe()
    df.loc[df.index[-1], "st_buy"] = False
    df.loc[df.index[-1], "st_sell"] = True
    df["all_bullish"] = False
    df["all_bearish"] = True
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    last_tag = out["enter_tag"].iloc[-1] if "enter_tag" in out.columns else None
    assert last_tag != "confirmed"


def test_scout_can_still_fire_when_confirmed_disabled(strategy, monkeypatch):
    """Scout uses ~mask_confirmed_long subtraction; with confirmed
    disabled the empty-mask should still allow scout to fire normally."""
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    monkeypatch.delenv("SUPERTREND_KELLY_MODE", raising=False)

    # Build scout-firing scenario: bull_just_formed + st_trend == -1
    df = _confirmed_long_dataframe()
    # All but last row: NOT all_bullish (so all_bullish "just formed" on last)
    df.loc[df.index[:-1], "all_bullish"] = False
    df.loc[df.index[-1], "all_bullish"] = True
    df.loc[df.index[-1], "st_trend"] = -1   # 15m bearish (scout entry condition)
    df.loc[df.index[-1], "st_buy"] = False  # not confirmed condition
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    # Scout should fire on this candle
    last_tag = out["enter_tag"].iloc[-1] if "enter_tag" in out.columns else None
    assert last_tag == "scout"


def test_disable_confirmed_doesnt_break_dataframe_shape(strategy, monkeypatch):
    """Disabling confirmed shouldn't drop columns or change row count."""
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    df = _confirmed_long_dataframe()
    n_before = len(df)
    out = strategy.populate_entry_trend(df, {"pair": "X"})
    assert len(out) == n_before
    assert "close" in out.columns   # original columns preserved
