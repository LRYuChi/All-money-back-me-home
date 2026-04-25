"""Tests for strategies.funding_alpha — R51 FR-as-alpha module."""
from __future__ import annotations

import pytest

from strategies.funding_alpha import (
    FR_EXTREME,
    FR_MILD,
    FR_NEUTRAL,
    FundingSignal,
    build_funding_signal,
    fr_bias_label,
    fr_confidence_label,
    fr_independent_entry,
    fr_signal_strength,
)


# =================================================================== #
# fr_signal_strength — sign + magnitude
# =================================================================== #
def test_strength_zero_for_zero_fr():
    assert fr_signal_strength(0.0) == 0.0


def test_strength_zero_for_neutral_fr():
    assert fr_signal_strength(0.0001) == 0.0
    assert fr_signal_strength(-0.0001) == 0.0


def test_strength_negative_for_positive_fr():
    """Positive FR (longs paying) → CONTRARIAN short bias = negative strength."""
    assert fr_signal_strength(0.0008) < 0


def test_strength_positive_for_negative_fr():
    """Negative FR (shorts paying) → CONTRARIAN long bias = positive strength."""
    assert fr_signal_strength(-0.0008) > 0


def test_strength_in_unit_interval():
    """No matter how extreme FR, strength stays in [-1, 1]."""
    for fr in [-0.05, -0.005, 0, 0.005, 0.05]:
        s = fr_signal_strength(fr)
        assert -1.0 <= s <= 1.0


def test_strength_saturates_for_blowoff():
    """At FR > 0.1%/8h (blowoff), strength approaches but doesn't exceed -1."""
    s = fr_signal_strength(0.05)   # 5% FR — extreme outlier
    assert -1.0 <= s < -0.95


def test_strength_symmetric():
    """fr_signal_strength(-x) == -fr_signal_strength(x) (signed)."""
    for fr_mag in [0.0003, 0.0008, 0.005]:
        assert fr_signal_strength(-fr_mag) == -fr_signal_strength(fr_mag)


def test_strength_monotonic_in_magnitude():
    """Larger |FR| → larger |strength|."""
    s_mild = abs(fr_signal_strength(0.0003))
    s_strong = abs(fr_signal_strength(0.0008))
    s_blowoff = abs(fr_signal_strength(0.005))
    assert s_mild < s_strong < s_blowoff


# =================================================================== #
# fr_confidence_label
# =================================================================== #
def test_confidence_neutral_zero():
    assert fr_confidence_label(0.0) == "neutral"


def test_confidence_neutral_small():
    assert fr_confidence_label(0.0001) == "neutral"


def test_confidence_mild():
    assert fr_confidence_label(0.0003) == "mild"


def test_confidence_extreme():
    assert fr_confidence_label(0.0007) == "extreme"


def test_confidence_blowoff():
    assert fr_confidence_label(0.002) == "blowoff"


def test_confidence_negative_uses_magnitude():
    """Negative FR uses absolute magnitude for label."""
    assert fr_confidence_label(-0.0003) == "mild"
    assert fr_confidence_label(-0.0007) == "extreme"
    assert fr_confidence_label(-0.002) == "blowoff"


# =================================================================== #
# fr_bias_label
# =================================================================== #
def test_bias_neutral_for_small_fr():
    assert fr_bias_label(0.00001) == "neutral"
    assert fr_bias_label(-0.00001) == "neutral"


def test_bias_short_for_positive_fr():
    """Positive FR → contra-signal = bias short."""
    assert fr_bias_label(0.001) == "short"


def test_bias_long_for_negative_fr():
    """Negative FR → contra-signal = bias long."""
    assert fr_bias_label(-0.001) == "long"


# =================================================================== #
# fr_independent_entry
# =================================================================== #
def test_no_entry_when_other_tf_signals_present():
    """Don't trigger if other timeframes have direction."""
    assert fr_independent_entry(0.005, all_tf_neutral=False) is None


def test_no_entry_when_fr_below_extreme():
    """Even with neutral TF, mild FR isn't enough."""
    assert fr_independent_entry(0.0003, all_tf_neutral=True) is None


def test_entry_short_when_extreme_positive_fr():
    """Extreme positive FR + neutral TF → short trigger."""
    assert fr_independent_entry(0.002, all_tf_neutral=True) == "short"


def test_entry_long_when_extreme_negative_fr():
    """Extreme negative FR + neutral TF → long trigger."""
    assert fr_independent_entry(-0.002, all_tf_neutral=True) == "long"


def test_entry_none_when_neutral_fr():
    assert fr_independent_entry(0, all_tf_neutral=True) is None


# =================================================================== #
# build_funding_signal — composite struct
# =================================================================== #
def test_build_signal_neutral():
    sig = build_funding_signal(0.0)
    assert sig.bias == "neutral"
    assert sig.confidence == "neutral"
    assert sig.strength == 0.0
    assert sig.independent_entry is None


def test_build_signal_blowoff_long():
    """Big negative FR with TF neutral → blowoff long signal."""
    sig = build_funding_signal(-0.005, all_tf_neutral=True)
    assert sig.bias == "long"
    assert sig.confidence == "blowoff"
    assert sig.strength > 0.5
    assert sig.independent_entry == "long"


def test_build_signal_extreme_short():
    sig = build_funding_signal(0.0008, all_tf_neutral=True)
    assert sig.bias == "short"
    assert sig.confidence == "extreme"
    assert sig.strength < 0
    # 0.0008 < FR_EXTREME (0.001) → no independent_entry
    assert sig.independent_entry is None


def test_build_signal_blowoff_short_with_independent_entry():
    sig = build_funding_signal(0.002, all_tf_neutral=True)
    assert sig.bias == "short"
    assert sig.confidence == "blowoff"
    assert sig.independent_entry == "short"


def test_build_signal_isolated_when_tf_active():
    """Extreme FR but TF active → no independent entry (normal flow uses
    strength as input only)."""
    sig = build_funding_signal(0.005, all_tf_neutral=False)
    assert sig.independent_entry is None
    # Strength still computed
    assert sig.strength < 0


# =================================================================== #
# Threshold constants exposed
# =================================================================== #
def test_thresholds_ordered():
    assert FR_NEUTRAL < FR_MILD < FR_EXTREME


def test_thresholds_match_real_values():
    """Sanity check that the constants match the design decision."""
    assert FR_NEUTRAL == 0.0002      # 0.02%/8h
    assert FR_MILD == 0.0005          # 0.05%/8h
    assert FR_EXTREME == 0.001        # 0.1%/8h


# =================================================================== #
# FundingSignal dataclass shape
# =================================================================== #
def test_funding_signal_is_frozen():
    """Defensive: callers can't mutate after construction."""
    sig = build_funding_signal(0.0)
    with pytest.raises((AttributeError, Exception)):
        sig.fr = 999
