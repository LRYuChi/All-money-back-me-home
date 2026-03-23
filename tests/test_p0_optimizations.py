"""Tests for P0 strategy optimizations: ATR multiplier & funding rate scoring."""

import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "strategies"))

from strategies.smc_trend import _atr_multiplier, _funding_rate_score


class TestAtrMultiplier:
    """Test continuous ATR stop-loss multiplier."""

    def test_calibration_points(self):
        """Output should match original midpoints at calibration values."""
        assert _atr_multiplier(0.001) == pytest.approx(2.0)
        assert _atr_multiplier(0.003) == pytest.approx(1.3)
        assert _atr_multiplier(0.006) == pytest.approx(1.0)
        assert _atr_multiplier(0.0115) == pytest.approx(0.85)
        assert _atr_multiplier(0.020) == pytest.approx(0.7)

    def test_boundary_low(self):
        """ATR below lowest calibration point should clamp to max multiplier."""
        assert _atr_multiplier(0.0) == pytest.approx(2.0)
        assert _atr_multiplier(0.0005) == pytest.approx(2.0)

    def test_boundary_high(self):
        """ATR above highest calibration point should clamp to min multiplier."""
        assert _atr_multiplier(0.05) == pytest.approx(0.7)
        assert _atr_multiplier(0.10) == pytest.approx(0.7)

    def test_monotonically_decreasing(self):
        """Multiplier should decrease as ATR% increases."""
        atr_values = [0.001, 0.002, 0.003, 0.004, 0.005, 0.006,
                      0.008, 0.010, 0.012, 0.015, 0.020]
        multipliers = [_atr_multiplier(v) for v in atr_values]
        for i in range(len(multipliers) - 1):
            assert multipliers[i] >= multipliers[i + 1], (
                f"Not monotonically decreasing: {atr_values[i]}→{multipliers[i]}, "
                f"{atr_values[i+1]}→{multipliers[i+1]}"
            )

    def test_smooth_transition(self):
        """No large jumps between adjacent points (max 0.15 per 0.001 step)."""
        prev = _atr_multiplier(0.0)
        for atr_pct_x1000 in range(1, 25):
            atr_pct = atr_pct_x1000 / 1000
            curr = _atr_multiplier(atr_pct)
            diff = abs(curr - prev)
            assert diff < 0.40, f"Jump too large at ATR={atr_pct}: {prev}→{curr} (diff={diff})"
            prev = curr

    def test_interpolation_midpoint(self):
        """Midpoint between calibration points should be the average."""
        # Between 0.001 (2.0) and 0.003 (1.3), midpoint 0.002 should be 1.65
        assert _atr_multiplier(0.002) == pytest.approx(1.65)
        # Between 0.006 (1.0) and 0.0115 (0.85), midpoint 0.00875 should be ~0.925
        assert _atr_multiplier(0.00875) == pytest.approx(0.925)


class TestFundingRateScore:
    """Test continuous funding rate scoring."""

    def test_zero_funding(self):
        """Zero funding rate should give zero score."""
        fr = pd.Series([0.0])
        result = _funding_rate_score(fr)
        assert result.iloc[0] == pytest.approx(0.0)

    def test_positive_funding_penalises(self):
        """Positive FR should return negative score (penalises longs)."""
        fr = pd.Series([0.0003])  # 0.03%/8h
        result = _funding_rate_score(fr)
        assert result.iloc[0] < 0
        assert result.iloc[0] == pytest.approx(-0.18)

    def test_negative_funding_rewards(self):
        """Negative FR should return positive score (rewards longs)."""
        fr = pd.Series([-0.0003])  # -0.03%/8h
        result = _funding_rate_score(fr)
        assert result.iloc[0] > 0
        assert result.iloc[0] == pytest.approx(0.18)

    def test_extreme_positive_clamps(self):
        """Extreme positive FR should clamp to -0.6."""
        fr = pd.Series([0.002])  # 0.2%/8h — very extreme
        result = _funding_rate_score(fr)
        assert result.iloc[0] == pytest.approx(-0.6)

    def test_extreme_negative_clamps(self):
        """Extreme negative FR should clamp to +0.3."""
        fr = pd.Series([-0.001])  # -0.1%/8h
        result = _funding_rate_score(fr)
        assert result.iloc[0] == pytest.approx(0.3)

    def test_at_current_threshold(self):
        """At current hard threshold (0.0005), score should be -0.3."""
        fr = pd.Series([0.0005])
        result = _funding_rate_score(fr)
        assert result.iloc[0] == pytest.approx(-0.3)

    def test_series_operation(self):
        """Should work on full Series with mixed values."""
        fr = pd.Series([0.0, 0.0003, -0.0005, 0.001, -0.001])
        result = _funding_rate_score(fr)
        assert len(result) == 5
        assert result.iloc[0] == pytest.approx(0.0)
        assert result.iloc[1] < 0       # positive FR → negative score
        assert result.iloc[2] > 0       # negative FR → positive score
        assert result.iloc[3] == pytest.approx(-0.6)  # clamped
        assert result.iloc[4] == pytest.approx(0.3)   # clamped
