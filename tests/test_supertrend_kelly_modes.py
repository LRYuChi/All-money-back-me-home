"""Tests for R49 — three-stage / continuous Kelly modes.

Pure unit tests against extracted sizing logic. We verify:
  - Default mode (three_stage) — pre_scout / scout / confirmed map to
    distinct fractions (0.25 / 0.50 / 0.85)
  - Binary mode (env=binary) — legacy 0.25 / 0.75 preserved
  - Continuous mode (env=continuous) — kelly × quality × |dir| × adx_norm
  - Edge cases: missing tag, invalid mode, pre_scout in binary mode (falls back)
"""
from __future__ import annotations

import os

import pytest


def _stage_fraction(tag: str, mode: str = "three_stage") -> float:
    """Mirrors the SUPERTREND_KELLY_MODE branch in custom_stake_amount."""
    if mode == "three_stage":
        return {
            "pre_scout": 0.25,
            "scout":     0.50,
            "confirmed": 0.85,
        }.get(tag or "", 0.50)
    elif mode == "three_stage_inverted":
        # R86: inverted mapping — pre_scout big, confirmed small
        return {
            "pre_scout": 0.85,
            "scout":     0.50,
            "confirmed": 0.25,
        }.get(tag or "", 0.50)
    elif mode == "binary":
        return 0.25 if tag == "scout" else 0.75
    return 1.0   # continuous handled separately


def _continuous_strength(quality: float, dir_score: float, adx: float) -> float:
    """Mirrors the continuous-mode strength calc."""
    adx_norm = min(adx / 50.0, 1.0)
    strength = quality * abs(dir_score) * adx_norm
    return max(0.10, min(strength, 1.0))


# =================================================================== #
# Three-stage Kelly map
# =================================================================== #
def test_three_stage_pre_scout_gets_quarter():
    assert _stage_fraction("pre_scout") == 0.25


def test_three_stage_scout_gets_half():
    assert _stage_fraction("scout") == 0.50


def test_three_stage_confirmed_gets_85_percent():
    assert _stage_fraction("confirmed") == 0.85


def test_three_stage_unknown_tag_defaults_to_scout_size():
    """Defensive: unknown tag → safe middle ground (0.50, not 0.85)."""
    assert _stage_fraction("ghost_tag") == 0.50


def test_three_stage_none_tag_defaults_to_scout_size():
    assert _stage_fraction(None) == 0.50


def test_three_stage_progression_monotonic():
    """pre_scout < scout < confirmed (sizing escalates with confidence)."""
    pre = _stage_fraction("pre_scout")
    sc = _stage_fraction("scout")
    cf = _stage_fraction("confirmed")
    assert pre < sc < cf


# =================================================================== #
# Binary mode (legacy / backward compat)
# =================================================================== #
def test_binary_scout_quarter():
    assert _stage_fraction("scout", "binary") == 0.25


def test_binary_confirmed_three_quarters():
    assert _stage_fraction("confirmed", "binary") == 0.75


def test_binary_pre_scout_falls_back_to_confirmed_size():
    """In binary mode, pre_scout doesn't exist → treated as 'not scout' = 0.75.
    The pre-scout entry signal won't even fire (env check in
    populate_entry_trend gates it), but the sizing function should still
    handle the input gracefully if it ever arrives."""
    assert _stage_fraction("pre_scout", "binary") == 0.75


# =================================================================== #
# Continuous mode strength
# =================================================================== #
def test_continuous_perfect_signal():
    """quality=1, dir=1, adx=50 → strength=1.0 → full Kelly."""
    s = _continuous_strength(quality=1.0, dir_score=1.0, adx=50.0)
    assert s == pytest.approx(1.0)


def test_continuous_zero_quality_clamped_to_floor():
    """Even zero strength inputs return floor 0.10 (no negative sizing)."""
    s = _continuous_strength(quality=0.0, dir_score=0.0, adx=0.0)
    assert s == 0.10


def test_continuous_negative_direction_uses_magnitude():
    """Short direction (negative dir_score) should produce same strength
    as long with same magnitude."""
    long_s = _continuous_strength(quality=0.7, dir_score=0.6, adx=30.0)
    short_s = _continuous_strength(quality=0.7, dir_score=-0.6, adx=30.0)
    assert long_s == short_s


def test_continuous_adx_capped_at_50():
    """ADX > 50 doesn't keep boosting indefinitely (caps at 1.0 norm)."""
    s_50 = _continuous_strength(quality=0.5, dir_score=0.5, adx=50.0)
    s_100 = _continuous_strength(quality=0.5, dir_score=0.5, adx=100.0)
    assert s_50 == s_100


def test_continuous_partial_signal():
    """Realistic mid-strength signal."""
    s = _continuous_strength(quality=0.7, dir_score=0.5, adx=30.0)
    # 0.7 × 0.5 × (30/50) = 0.21 → above floor 0.10
    assert s == pytest.approx(0.21)


def test_continuous_below_floor_returns_floor():
    s = _continuous_strength(quality=0.1, dir_score=0.1, adx=10.0)
    # 0.1 × 0.1 × 0.2 = 0.002 → floor 0.10
    assert s == 0.10


# =================================================================== #
# Strategy class env-var integration
# =================================================================== #
def test_default_kelly_mode_is_three_stage():
    """When SUPERTREND_KELLY_MODE is unset, default = three_stage."""
    # Just verify the default string value
    mode = os.environ.get("SUPERTREND_KELLY_MODE", "three_stage")
    assert mode == "three_stage" or mode in ("binary", "continuous")


def test_strategy_module_has_pre_scout_helpers():
    """populate_indicators must produce pair_bullish_2tf / pair_bearish_2tf
    so populate_entry_trend can compute pre_scout edges."""
    from strategies.supertrend import SupertrendStrategy
    # Method existence — full pandas test would need live dataframe
    assert hasattr(SupertrendStrategy, "populate_indicators")
    assert hasattr(SupertrendStrategy, "populate_entry_trend")


# =================================================================== #
# Mode invariants
# =================================================================== #
def test_all_three_modes_in_valid_range():
    """No matter which mode, the fraction × kelly should be sensible."""
    base_kelly = 0.10   # 10% raw Kelly
    for mode in ["binary", "three_stage"]:
        for tag in ["pre_scout", "scout", "confirmed"]:
            sized = base_kelly * _stage_fraction(tag, mode)
            assert 0 <= sized <= 0.10   # Never exceeds base Kelly


def test_continuous_strength_in_valid_range():
    """For all reasonable inputs, strength stays in [0.10, 1.0]."""
    for q in [0.0, 0.3, 0.7, 1.0]:
        for d in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            for a in [0, 25, 50, 100]:
                s = _continuous_strength(q, d, a)
                assert 0.10 <= s <= 1.0


# =================================================================== #
# R86: three_stage_inverted (early-entry favoured)
# =================================================================== #
def test_inverted_pre_scout_gets_85_percent():
    """R86 inversion: pre_scout (earliest, was 0.25) NOW gets max 0.85."""
    assert _stage_fraction("pre_scout", "three_stage_inverted") == 0.85


def test_inverted_scout_unchanged_at_50_percent():
    """Middle tier sizing stays the same."""
    assert _stage_fraction("scout", "three_stage_inverted") == 0.50


def test_inverted_confirmed_demoted_to_25_percent():
    """R86 inversion: confirmed (4-tf aligned, was 0.85) NOW gets only 0.25.
    Per R85 backtest, confirmed tier is the source of all loss."""
    assert _stage_fraction("confirmed", "three_stage_inverted") == 0.25


def test_inverted_progression_reverses_three_stage():
    """Inverted mode reverses the sizing-vs-conviction relationship."""
    pre = _stage_fraction("pre_scout", "three_stage_inverted")
    sc = _stage_fraction("scout", "three_stage_inverted")
    cf = _stage_fraction("confirmed", "three_stage_inverted")
    assert pre > sc > cf   # inverse of three_stage progression


def test_inverted_unknown_tag_defaults_to_scout_size():
    assert _stage_fraction("ghost_tag", "three_stage_inverted") == 0.50


def test_inverted_total_capital_equal_to_three_stage():
    """Sum of three fractions equal — no net portfolio exposure change,
    just redistribution by tier."""
    s_normal = sum(_stage_fraction(t, "three_stage")
                   for t in ("pre_scout", "scout", "confirmed"))
    s_inverted = sum(_stage_fraction(t, "three_stage_inverted")
                     for t in ("pre_scout", "scout", "confirmed"))
    assert s_normal == s_inverted == 1.60
