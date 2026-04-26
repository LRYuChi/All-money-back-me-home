"""R108 — NO_FIRES_24H alert now includes actionable env-tuning advice.

Each branch of _suggest_for_failure maps a R66 EvaluationEvent dominant
failure_reason to specific advice that names the controlling env var.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api", "src"))

from routers.supertrend import _suggest_for_failure   # noqa: E402


# =================================================================== #
# Volume gate (R89)
# =================================================================== #

def test_vol_gate_at_minimum_says_no_tweak_helps():
    out = _suggest_for_failure("vol<=1*ma")
    assert "minimum effective" in out
    assert "wait for regime shift" in out


def test_vol_gate_above_minimum_suggests_lowering():
    out = _suggest_for_failure("vol<=1.2*ma")
    assert "SUPERTREND_VOL_MULT" in out
    assert "1.0" in out
    assert "r89" in out.lower()


# =================================================================== #
# Quality gate (R91)
# =================================================================== #

def test_quality_gate_suggests_quality_min_env():
    out = _suggest_for_failure("quality<=0.5")
    assert "SUPERTREND_QUALITY_MIN" in out
    assert "0.4" in out
    assert "r91" in out.lower()


# =================================================================== #
# ADX gate (R91)
# =================================================================== #

def test_adx_gate_suggests_adx_min_env():
    out = _suggest_for_failure("adx<=25")
    assert "SUPERTREND_ADX_MIN" in out
    assert "20" in out


# =================================================================== #
# ATR rising (R91)
# =================================================================== #

def test_atr_not_rising_suggests_require_atr_rising_env():
    out = _suggest_for_failure("atr_not_rising")
    assert "SUPERTREND_REQUIRE_ATR_RISING" in out
    assert "0" in out
    assert "CAUTION" in out


# =================================================================== #
# Multi-tf alignment — strategy waiting, no env tweak
# =================================================================== #

@pytest.mark.parametrize("reason", [
    "all_bullish=False",
    "all_bearish=False",
    "bull_just_formed=False",
    "bear_just_formed=False",
    "pair_bullish_2tf_just_formed=False",
    "pair_bearish_2tf_just_formed=False",
    "st_buy=False",
    "st_sell=False",
    "st_trend!=-1",
    "st_trend!=1",
    "st_1h_already_aligned_long",
    "st_1h_already_aligned_short",
])
def test_multi_tf_alignment_failures_explain_no_action_needed(reason):
    out = _suggest_for_failure(reason)
    assert "Multi-timeframe alignment" in out
    assert "WAITING" in out
    assert "No env tweak" in out


# =================================================================== #
# Funding rate filter
# =================================================================== #

def test_fr_blocks_long_suggests_disabling_alpha():
    out = _suggest_for_failure("fr_blocks_long")
    assert "SUPERTREND_FR_ALPHA" in out
    assert "opt-in alpha" in out


# =================================================================== #
# Portfolio guards (regime / concentration / correlation / CB)
# =================================================================== #

def test_regime_guard_explains_designed_behaviour():
    out = _suggest_for_failure("regime: DEAD")
    assert "regime" in out.lower()


def test_direction_concentration_explains_designed_behaviour():
    out = _suggest_for_failure("direction_concentration: 2 open, cap 2")
    assert "concentration" in out.lower()


def test_cb_tripped_explains_account_level_breaker():
    out = _suggest_for_failure("CB tripped — skipping entry")
    assert "circuit breaker" in out


def test_confirmed_disabled_R87_explains_no_action():
    out = _suggest_for_failure("confirmed_disabled_R87")
    assert "R87" in out
    assert "No action needed" in out


# =================================================================== #
# Unknown failure — graceful fallback
# =================================================================== #

def test_unknown_failure_falls_back_to_evaluations_endpoint():
    out = _suggest_for_failure("brand_new_failure_mode_42")
    assert "/api/supertrend/evaluations" in out
    assert "regime mismatch" in out
