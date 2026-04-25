"""Tests for R50 — weighted multi-signal exit logic.

Replicates _exit_signal_score's pure logic for unit testing without
needing Freqtrade IStrategy machinery.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import pytest


# Replicate the exact weights + scoring logic from supertrend.py
W_1D = 0.30
W_4H = 0.25
W_15M = 0.25
W_ADX = 0.20


def _build_df(rows: list[dict]) -> pd.DataFrame:
    """Build the analyzed dataframe shape used by _exit_signal_score."""
    return pd.DataFrame(rows)


def _exit_score(df: pd.DataFrame, is_long: bool) -> tuple[float, dict]:
    """Pure replica of SupertrendStrategy._exit_signal_score for testing."""
    if len(df) < 6:
        return 0.0, {"reason": "insufficient_data"}

    last = df.iloc[-1]
    breakdown: dict[str, float] = {}

    # Factor 1: 1D reversal
    st_1d_val = last.get("st_1d", 0)
    f1 = 1.0 if (
        (is_long and st_1d_val == -1)
        or (not is_long and st_1d_val == 1)
    ) else 0.0
    breakdown["1d_reversal"] = f1

    # Factor 2: 4H dir crossed zero
    dir_now = float(last.get("dir_4h_score", 0.0))
    if len(df) >= 4:
        dir_past = float(df.iloc[-4].get("dir_4h_score", 0.0))
    else:
        dir_past = dir_now
    f2 = 0.0
    if is_long:
        if dir_past > 0 and dir_now < 0:
            f2 = 1.0
        elif dir_past > 0 and dir_now < 0.1:
            f2 = 0.5
    else:
        if dir_past < 0 and dir_now > 0:
            f2 = 1.0
        elif dir_past < 0 and dir_now > -0.1:
            f2 = 0.5
    breakdown["4h_dir_reversal"] = f2

    # Factor 3: 15m consecutive
    recent_15m = df.iloc[-3:]["st_trend"].values
    target_against = -1 if is_long else 1
    consec = sum(1 for v in recent_15m if v == target_against)
    if consec == 0:
        f3 = 0.0
    elif consec == 1:
        f3 = 0.25
    elif consec == 2:
        f3 = 0.5
    else:
        f3 = 1.0
    breakdown["15m_consecutive_against"] = f3

    # Factor 4: ADX declining
    adx_now = float(last.get("adx", 25.0))
    if len(df) >= 7:
        adx_past = float(df.iloc[-7].get("adx", 25.0))
    else:
        adx_past = adx_now
    adx_drop = adx_past - adx_now
    f4 = 0.0
    if adx_drop > 8:
        f4 = 1.0
    elif adx_drop > 5:
        f4 = 0.7
    elif adx_drop > 2:
        f4 = 0.3
    breakdown["adx_declining"] = f4

    score = W_1D * f1 + W_4H * f2 + W_15M * f3 + W_ADX * f4
    return score, breakdown


# =================================================================== #
# Insufficient data
# =================================================================== #
def test_insufficient_data_returns_zero():
    df = _build_df([{"st_trend": 1, "st_1d": 1, "dir_4h_score": 0.5, "adx": 30}])
    score, brk = _exit_score(df, is_long=True)
    assert score == 0.0
    assert brk["reason"] == "insufficient_data"


# =================================================================== #
# Single-factor verification (each factor in isolation)
# =================================================================== #
def _baseline_holding_long_df() -> pd.DataFrame:
    """7 bars of perfect bullish — no exit signals fire."""
    return _build_df([
        {"st_trend": 1, "st_1d": 1, "dir_4h_score": 0.5, "adx": 30},
    ] * 7)


def test_baseline_no_exit_signals():
    df = _baseline_holding_long_df()
    score, _ = _exit_score(df, is_long=True)
    assert score == 0.0


def test_factor_1_1d_reversal_only():
    df = _baseline_holding_long_df()
    df.iloc[-1, df.columns.get_loc("st_1d")] = -1   # 1D flipped
    score, brk = _exit_score(df, is_long=True)
    assert brk["1d_reversal"] == 1.0
    assert score == pytest.approx(W_1D)   # 0.30


def test_factor_2_4h_dir_full_reversal():
    df = _baseline_holding_long_df()
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = 0.6   # was bullish 3 bars ago
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = -0.2  # now bearish
    score, brk = _exit_score(df, is_long=True)
    assert brk["4h_dir_reversal"] == 1.0
    assert score == pytest.approx(W_4H)


def test_factor_2_4h_partial_reversal():
    """Past was bullish, now < 0.1 but not negative → partial."""
    df = _baseline_holding_long_df()
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = 0.5
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = 0.05
    score, brk = _exit_score(df, is_long=True)
    assert brk["4h_dir_reversal"] == 0.5
    assert score == pytest.approx(W_4H * 0.5)


def test_factor_3_15m_one_against():
    df = _baseline_holding_long_df()
    df.iloc[-1, df.columns.get_loc("st_trend")] = -1
    score, brk = _exit_score(df, is_long=True)
    assert brk["15m_consecutive_against"] == 0.25
    assert score == pytest.approx(W_15M * 0.25)


def test_factor_3_15m_two_against():
    df = _baseline_holding_long_df()
    df.iloc[-2:, df.columns.get_loc("st_trend")] = -1
    score, brk = _exit_score(df, is_long=True)
    assert brk["15m_consecutive_against"] == 0.5


def test_factor_3_15m_three_against():
    df = _baseline_holding_long_df()
    df.iloc[-3:, df.columns.get_loc("st_trend")] = -1
    score, brk = _exit_score(df, is_long=True)
    assert brk["15m_consecutive_against"] == 1.0


def test_factor_4_adx_steep_drop():
    df = _baseline_holding_long_df()
    df.iloc[-7, df.columns.get_loc("adx")] = 40
    df.iloc[-1, df.columns.get_loc("adx")] = 30   # drop 10
    score, brk = _exit_score(df, is_long=True)
    assert brk["adx_declining"] == 1.0


def test_factor_4_adx_moderate_drop():
    df = _baseline_holding_long_df()
    df.iloc[-7, df.columns.get_loc("adx")] = 36
    df.iloc[-1, df.columns.get_loc("adx")] = 30   # drop 6
    score, brk = _exit_score(df, is_long=True)
    assert brk["adx_declining"] == 0.7


def test_factor_4_adx_no_drop():
    df = _baseline_holding_long_df()
    df.iloc[-7, df.columns.get_loc("adx")] = 30
    df.iloc[-1, df.columns.get_loc("adx")] = 30
    _, brk = _exit_score(df, is_long=True)
    assert brk["adx_declining"] == 0.0


# =================================================================== #
# Composite scenarios — boundary thresholds
# =================================================================== #
def test_full_close_threshold_075():
    """1D reversal + full 4H reversal + 15m 3 against → 0.30+0.25+0.25 = 0.80
    (above 0.75 → full close)."""
    df = _baseline_holding_long_df()
    df.iloc[-1, df.columns.get_loc("st_1d")] = -1
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = 0.6
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = -0.2
    df.iloc[-3:, df.columns.get_loc("st_trend")] = -1
    score, _ = _exit_score(df, is_long=True)
    assert score >= 0.75


def test_partial_zone_three_factors_minus_full_signal():
    """1D reversal + 4H partial + 15m 1 against = 0.30 + 0.125 + 0.0625 = 0.49.
    Just under 0.5 — would be hold, NOT partial."""
    df = _baseline_holding_long_df()
    df.iloc[-1, df.columns.get_loc("st_1d")] = -1
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = 0.5
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = 0.05  # partial
    df.iloc[-1, df.columns.get_loc("st_trend")] = -1   # 1 bar
    score, _ = _exit_score(df, is_long=True)
    # 0.30 + 0.125 + 0.0625 = 0.4875 → just under 0.5 → hold
    assert 0.45 < score < 0.50


def test_partial_zone_around_065():
    """1D reversal + 4H partial + 15m 2 against + ADX moderate
    = 0.30 + 0.125 + 0.125 + 0.14 = 0.69 → partial-reduce zone."""
    df = _baseline_holding_long_df()
    df.iloc[-1, df.columns.get_loc("st_1d")] = -1
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = 0.5
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = 0.05
    df.iloc[-2:, df.columns.get_loc("st_trend")] = -1
    df.iloc[-7, df.columns.get_loc("adx")] = 36
    df.iloc[-1, df.columns.get_loc("adx")] = 30
    score, _ = _exit_score(df, is_long=True)
    assert 0.50 <= score < 0.75   # partial-reduce zone


def test_hold_zone_only_one_weak_factor():
    """Only ADX moderate decline. 0.14 → hold."""
    df = _baseline_holding_long_df()
    df.iloc[-7, df.columns.get_loc("adx")] = 36
    df.iloc[-1, df.columns.get_loc("adx")] = 30
    score, _ = _exit_score(df, is_long=True)
    assert score < 0.50


# =================================================================== #
# Short position symmetry
# =================================================================== #
def _baseline_holding_short_df() -> pd.DataFrame:
    return _build_df([
        {"st_trend": -1, "st_1d": -1, "dir_4h_score": -0.5, "adx": 30},
    ] * 7)


def test_short_baseline_no_exit():
    df = _baseline_holding_short_df()
    score, _ = _exit_score(df, is_long=False)
    assert score == 0.0


def test_short_1d_reversal_to_bullish():
    df = _baseline_holding_short_df()
    df.iloc[-1, df.columns.get_loc("st_1d")] = 1   # bullish 1D against short
    score, brk = _exit_score(df, is_long=False)
    assert brk["1d_reversal"] == 1.0


def test_short_4h_reversal_to_positive():
    df = _baseline_holding_short_df()
    df.iloc[-4, df.columns.get_loc("dir_4h_score")] = -0.5
    df.iloc[-1, df.columns.get_loc("dir_4h_score")] = 0.2   # turned positive
    score, brk = _exit_score(df, is_long=False)
    assert brk["4h_dir_reversal"] == 1.0


def test_short_15m_consecutive_bullish_bars():
    df = _baseline_holding_short_df()
    df.iloc[-3:, df.columns.get_loc("st_trend")] = 1
    score, brk = _exit_score(df, is_long=False)
    assert brk["15m_consecutive_against"] == 1.0


# =================================================================== #
# Score range invariants
# =================================================================== #
def test_score_in_unit_interval():
    """All combinations produce score in [0, 1]."""
    for st_1d_val in [-1, 0, 1]:
        for dir_now in [-0.5, 0, 0.5]:
            for trend in [-1, 1]:
                for adx_now in [10, 30, 50]:
                    df = _build_df([
                        {"st_trend": trend, "st_1d": st_1d_val,
                         "dir_4h_score": dir_now, "adx": adx_now}
                    ] * 7)
                    score_long, _ = _exit_score(df, is_long=True)
                    score_short, _ = _exit_score(df, is_long=False)
                    assert 0 <= score_long <= 1
                    assert 0 <= score_short <= 1


# =================================================================== #
# Strategy class wires the score helper
# =================================================================== #
def test_strategy_class_has_score_method():
    from strategies.supertrend import SupertrendStrategy
    assert hasattr(SupertrendStrategy, "_exit_signal_score")


def test_strategy_class_weights_sum_to_one():
    from strategies.supertrend import SupertrendStrategy
    total = (
        SupertrendStrategy._EXIT_WEIGHT_1D
        + SupertrendStrategy._EXIT_WEIGHT_4H
        + SupertrendStrategy._EXIT_WEIGHT_15M
        + SupertrendStrategy._EXIT_WEIGHT_ADX
    )
    assert total == pytest.approx(1.0)
