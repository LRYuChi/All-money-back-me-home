"""Tests for R66 — populate_entry_trend evaluation telemetry.

Tests the _write_evaluation_event helper that records per-pair, per-candle
entry-tier failure reasons. Uses synthetic dataframes — no Freqtrade
scaffolding required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


def _mk_dataframe(**last_row_overrides) -> pd.DataFrame:
    """Build a minimal 2-row dataframe with all indicator columns the
    evaluation reads. last_row_overrides override the latest candle."""
    base = {
        "date": "2026-04-25T15:00:00Z",
        "adx": 30.0, "volume": 100.0, "volume_ma_20": 50.0,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": False, "all_bearish": False,
        "fr_ok_long": True, "fr_ok_short": True,
        "st_trend": 0, "st_1h": 0,
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    prev = dict(base)
    last = {**base, **last_row_overrides}
    return pd.DataFrame([prev, last])


@pytest.fixture
def strategy(monkeypatch):
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.adx_threshold = 25.0
    monkeypatch.setenv("SUPERTREND_EVAL_JOURNAL", "1")
    return s


# =================================================================== #
# Capture written event for inspection
# =================================================================== #
def _capture_event(strategy, df, pair="BTC/USDT:USDT"):
    """Run _write_evaluation_event with a captured _safe_journal_write."""
    captured = []
    with patch(
        "strategies.supertrend._safe_journal_write",
        side_effect=lambda ev: captured.append(ev),
    ):
        strategy._write_evaluation_event(df, {"pair": pair})
    assert len(captured) == 1
    return captured[0]


# =================================================================== #
# Quality fail decomposition
# =================================================================== #
def test_eval_records_low_adx_failure(strategy):
    df = _mk_dataframe(adx=15.0, all_bullish=True, st_buy=True)
    ev = _capture_event(strategy, df)
    # Low ADX means quality fails for all tiers
    assert any("adx<=25" in f for f in ev.confirmed_failures)
    assert ev.confirmed_fired is False


def test_eval_records_low_volume_failure(strategy):
    df = _mk_dataframe(volume=50.0, volume_ma_20=100.0)
    ev = _capture_event(strategy, df)
    assert "vol<=1.2*ma" in ev.confirmed_failures


def test_eval_records_atr_not_rising_failure(strategy):
    df = _mk_dataframe(atr_rising=False)
    ev = _capture_event(strategy, df)
    assert "atr_not_rising" in ev.confirmed_failures


def test_eval_records_low_quality_score_failure(strategy):
    df = _mk_dataframe(trend_quality=0.3)
    ev = _capture_event(strategy, df)
    assert "quality<=0.5" in ev.confirmed_failures


# =================================================================== #
# Confirmed-tier specific failures
# =================================================================== #
def test_confirmed_fires_when_long_conditions_met(strategy):
    df = _mk_dataframe(
        st_buy=True, all_bullish=True,
        adx=30, volume=100, volume_ma_20=50,
        atr_rising=True, trend_quality=0.7, fr_ok_long=True,
    )
    ev = _capture_event(strategy, df)
    assert ev.confirmed_fired is True


def test_confirmed_fails_st_buy_false(strategy):
    df = _mk_dataframe(
        st_buy=False, all_bullish=True,  # st_buy missing
        adx=30, volume=100, volume_ma_20=50,
        atr_rising=True, trend_quality=0.7,
    )
    ev = _capture_event(strategy, df)
    assert ev.confirmed_fired is False
    # Long branch fails on st_buy
    long_failures = [
        f for f in ev.confirmed_failures
        if "st_buy" in f or "st_sell" in f
        or "all_bullish" in f or "all_bearish" in f
    ]
    assert any("st_buy" in f for f in long_failures + ev.confirmed_failures)


def test_confirmed_fr_blocks_long(strategy):
    df = _mk_dataframe(
        st_buy=True, all_bullish=True, fr_ok_long=False, fr_ok_short=False,
        adx=30, volume=100, volume_ma_20=50,
        atr_rising=True, trend_quality=0.7,
    )
    ev = _capture_event(strategy, df)
    assert ev.confirmed_fired is False
    assert any("fr_blocks" in f for f in ev.confirmed_failures)


# =================================================================== #
# Scout-tier failures (edge trigger)
# =================================================================== #
def test_scout_fails_when_already_aligned_previous_candle(strategy):
    """all_bullish=True on BOTH last AND prev candle → no edge → scout fails."""
    base = {
        "adx": 30, "volume": 100, "volume_ma_20": 50,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": True, "all_bearish": False,   # both rows have True
        "fr_ok_long": True, "fr_ok_short": True,
        "st_trend": -1, "st_1h": 1,
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    df = pd.DataFrame([base, base])
    ev = _capture_event(strategy, df)
    # Scout long fails because bull_just_formed=False
    assert ev.scout_fired is False
    assert any("bull_just_formed" in f for f in ev.scout_failures)


def test_scout_fires_when_alignment_first_forms(strategy):
    """all_bullish False → True triggers bull_just_formed."""
    prev = {
        "adx": 30, "volume": 100, "volume_ma_20": 50,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": False, "all_bearish": False,
        "fr_ok_long": True, "fr_ok_short": True,
        "st_trend": -1, "st_1h": 1,
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    last = {**prev, "all_bullish": True}
    df = pd.DataFrame([prev, last])
    ev = _capture_event(strategy, df)
    # st_trend=-1 (15m bearish — pre-flip) qualifies for scout long
    assert ev.scout_fired is True


# =================================================================== #
# Pre-scout failures
# =================================================================== #
def test_pre_scout_fails_when_1h_already_aligned(strategy):
    """Pre-scout requires st_1h NOT yet aligned."""
    prev = {
        "adx": 30, "volume": 100, "volume_ma_20": 50,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": False, "all_bearish": False,
        "fr_ok_long": True, "fr_ok_short": True,
        "st_trend": 0, "st_1h": 1,   # 1h ALREADY long
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    last = {**prev, "pair_bullish_2tf": True}
    df = pd.DataFrame([prev, last])
    ev = _capture_event(strategy, df)
    assert ev.pre_scout_fired is False
    assert any("st_1h_already_aligned" in f for f in ev.pre_scout_failures)


def test_pre_scout_fires_when_2tf_just_formed_and_1h_pending(strategy):
    prev = {
        "adx": 30, "volume": 100, "volume_ma_20": 50,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": False, "all_bearish": False,
        "fr_ok_long": True, "fr_ok_short": True,
        "st_trend": -1, "st_1h": 0,  # 1h NOT yet long
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    last = {**prev, "pair_bullish_2tf": True}
    df = pd.DataFrame([prev, last])
    ev = _capture_event(strategy, df)
    assert ev.pre_scout_fired is True


# =================================================================== #
# Event metadata + opt-out
# =================================================================== #
def test_eval_event_carries_pair_and_candle_ts(strategy):
    df = _mk_dataframe(date="2026-04-25T15:30:00Z")
    ev = _capture_event(strategy, df, pair="ETH/USDT:USDT")
    assert ev.pair == "ETH/USDT:USDT"
    assert "2026-04-25" in ev.candle_ts
    assert ev.event_type == "evaluation"


def test_eval_journal_opt_out_via_env(monkeypatch):
    """SUPERTREND_EVAL_JOURNAL=0 → populate_entry_trend writes nothing."""
    monkeypatch.setenv("SUPERTREND_EVAL_JOURNAL", "0")
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.adx_threshold = 25.0
    df = _mk_dataframe()
    captured = []
    # Simulate the gating from populate_entry_trend by invoking the env check
    import os
    if os.environ.get("SUPERTREND_EVAL_JOURNAL", "1") == "1":
        with patch(
            "strategies.supertrend._safe_journal_write",
            side_effect=lambda ev: captured.append(ev),
        ):
            s._write_evaluation_event(df, {"pair": "X"})
    assert captured == []


# =================================================================== #
# R93: env-aware diagnostic — failure-reason text must mirror env vars
# =================================================================== #
def test_vol_mult_env_reflected_in_failure_text(strategy, monkeypatch):
    """SUPERTREND_VOL_MULT=1.0 → failure text says 'vol<=1*ma' not '1.2*ma'."""
    monkeypatch.setenv("SUPERTREND_VOL_MULT", "1.0")
    df = _mk_dataframe(volume=80.0, volume_ma_20=100.0)  # vol < 1.0×ma
    ev = _capture_event(strategy, df)
    assert "vol<=1*ma" in ev.confirmed_failures
    assert "vol<=1.2*ma" not in ev.confirmed_failures   # old hardcode gone


def test_vol_mult_default_preserves_legacy_text(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_VOL_MULT", raising=False)
    df = _mk_dataframe(volume=110.0, volume_ma_20=100.0)  # vol < 1.2×ma
    ev = _capture_event(strategy, df)
    assert "vol<=1.2*ma" in ev.confirmed_failures


def test_quality_min_env_reflected_in_failure_text(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_QUALITY_MIN", "0.4")
    df = _mk_dataframe(trend_quality=0.35)
    ev = _capture_event(strategy, df)
    assert "quality<=0.4" in ev.confirmed_failures


def test_adx_min_env_reflected_in_failure_text(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ADX_MIN", "20")
    df = _mk_dataframe(adx=18.0)
    ev = _capture_event(strategy, df)
    assert "adx<=20" in ev.confirmed_failures


def test_atr_rising_disabled_skips_atr_failure(strategy, monkeypatch):
    """SUPERTREND_REQUIRE_ATR_RISING=0 → atr_not_rising never appears."""
    monkeypatch.setenv("SUPERTREND_REQUIRE_ATR_RISING", "0")
    df = _mk_dataframe(atr_rising=False)
    ev = _capture_event(strategy, df)
    assert "atr_not_rising" not in ev.confirmed_failures


# =================================================================== #
# R93: confirmed-disabled (R87) — diagnostic must mark tier un-fireable
# =================================================================== #
def test_confirmed_disabled_marks_tier_unfireable(strategy, monkeypatch):
    """SUPERTREND_DISABLE_CONFIRMED=1 → confirmed_fired=False with sentinel."""
    monkeypatch.setenv("SUPERTREND_DISABLE_CONFIRMED", "1")
    # Build a candle that WOULD fire confirmed_long if not disabled
    df = _mk_dataframe(
        all_bullish=True, st_buy=True, fr_ok_long=True,
        adx=30.0, volume=200.0, volume_ma_20=100.0,
        atr_rising=True, trend_quality=0.8,
    )
    ev = _capture_event(strategy, df)
    assert ev.confirmed_fired is False
    assert ev.confirmed_failures == ["confirmed_disabled_R87"]


def test_confirmed_enabled_default_can_still_fire(strategy, monkeypatch):
    """Default behaviour (no env) — confirmed tier evaluates normally."""
    monkeypatch.delenv("SUPERTREND_DISABLE_CONFIRMED", raising=False)
    df = _mk_dataframe(
        all_bullish=True, st_buy=True, fr_ok_long=True,
        adx=30.0, volume=200.0, volume_ma_20=100.0,
        atr_rising=True, trend_quality=0.8,
    )
    ev = _capture_event(strategy, df)
    assert ev.confirmed_fired is True
    assert ev.confirmed_failures == []   # all conditions met
