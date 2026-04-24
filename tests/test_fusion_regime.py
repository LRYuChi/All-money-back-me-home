"""Tests for fusion.regime — RegimeDetector + decision matrix."""
from __future__ import annotations

import pytest

from fusion.regime import (
    MarketContext,
    Regime,
    RegimeDetector,
    detect_regime,
)


# ================================================================== #
# CRISIS overrides — highest priority
# ================================================================== #
def test_crisis_via_high_vix():
    ctx = MarketContext(vix=40, btc_price=50_000, btc_ma200=50_000,
                        btc_ma200_slope=0.001, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.CRISIS


def test_crisis_via_daily_drawdown():
    ctx = MarketContext(daily_dd_pct=0.07, btc_price=46_500, btc_ma200=50_000,
                        btc_ma200_slope=-0.001, btc_realized_vol=1.0)
    assert detect_regime(ctx) == Regime.CRISIS


def test_crisis_at_exact_threshold_does_not_fire():
    """Boundary: VIX = 35 exactly; rule is strict > so no crisis."""
    ctx = MarketContext(vix=35.0, btc_price=51_000, btc_ma200=50_000,
                        btc_ma200_slope=0.001, btc_realized_vol=0.5)
    assert detect_regime(ctx) != Regime.CRISIS


def test_crisis_not_triggered_with_normal_vix_and_dd():
    ctx = MarketContext(vix=15, daily_dd_pct=0.01, btc_price=51_000,
                        btc_ma200=50_000, btc_ma200_slope=0.001,
                        btc_realized_vol=0.5)
    assert detect_regime(ctx) != Regime.CRISIS


# ================================================================== #
# Bull regimes
# ================================================================== #
def test_bull_trending_low_vol():
    ctx = MarketContext(btc_price=55_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=0.4)
    assert detect_regime(ctx) == Regime.BULL_TRENDING


def test_bull_choppy_high_vol():
    ctx = MarketContext(btc_price=55_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=0.9)
    assert detect_regime(ctx) == Regime.BULL_CHOPPY


def test_bull_no_vol_data_defaults_to_choppy():
    """Defensive: missing vol → CHOPPY (don't aggressively call TRENDING)."""
    ctx = MarketContext(btc_price=55_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=None)
    assert detect_regime(ctx) == Regime.BULL_CHOPPY


# ================================================================== #
# Bear regimes
# ================================================================== #
def test_bear_trending_low_vol():
    ctx = MarketContext(btc_price=45_000, btc_ma200=50_000,
                        btc_ma200_slope=-0.002, btc_realized_vol=0.4)
    assert detect_regime(ctx) == Regime.BEAR_TRENDING


def test_bear_choppy_high_vol():
    ctx = MarketContext(btc_price=45_000, btc_ma200=50_000,
                        btc_ma200_slope=-0.002, btc_realized_vol=0.9)
    assert detect_regime(ctx) == Regime.BEAR_CHOPPY


# ================================================================== #
# Sideways regimes — near MA OR flat slope
# ================================================================== #
def test_sideways_low_vol_near_ma():
    """Price within near_ma_band of MA → sideways regardless of slope."""
    # 50,500 / 50,000 = 1.01 → 1% distance, < default 2% band
    ctx = MarketContext(btc_price=50_500, btc_ma200=50_000,
                        btc_ma200_slope=0.003, btc_realized_vol=0.3)
    assert detect_regime(ctx) == Regime.SIDEWAYS_LOW_VOL


def test_sideways_high_vol_near_ma():
    ctx = MarketContext(btc_price=50_500, btc_ma200=50_000,
                        btc_ma200_slope=0.003, btc_realized_vol=0.55)
    assert detect_regime(ctx) == Regime.SIDEWAYS_HIGH_VOL


def test_sideways_low_vol_flat_slope():
    """Far from MA price-wise but slope is flat → still sideways."""
    ctx = MarketContext(btc_price=55_000, btc_ma200=50_000,
                        btc_ma200_slope=0.0001, btc_realized_vol=0.3)
    # |slope| < 0.0005 = flat
    assert detect_regime(ctx) == Regime.SIDEWAYS_LOW_VOL


def test_sideways_no_vol_defaults_to_high_vol():
    ctx = MarketContext(btc_price=50_500, btc_ma200=50_000,
                        btc_ma200_slope=0.003, btc_realized_vol=None)
    assert detect_regime(ctx) == Regime.SIDEWAYS_HIGH_VOL


# ================================================================== #
# Mixed signals
# ================================================================== #
def test_price_above_ma_but_slope_falling():
    """Price > MA but MA falling — recent strength against established
    weakness → BULL_CHOPPY (not trending)."""
    ctx = MarketContext(btc_price=53_000, btc_ma200=50_000,
                        btc_ma200_slope=-0.002, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.BULL_CHOPPY


def test_price_below_ma_but_slope_rising():
    """Price < MA but MA rising — recent weakness against established
    strength → BEAR_CHOPPY (not trending)."""
    ctx = MarketContext(btc_price=47_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.BEAR_CHOPPY


# ================================================================== #
# Missing data → UNKNOWN
# ================================================================== #
def test_unknown_when_price_missing():
    ctx = MarketContext(btc_ma200=50_000, btc_ma200_slope=0.002, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.UNKNOWN


def test_unknown_when_ma_missing():
    ctx = MarketContext(btc_price=50_000, btc_ma200_slope=0.002, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.UNKNOWN


def test_unknown_when_slope_missing():
    ctx = MarketContext(btc_price=50_000, btc_ma200=50_000, btc_realized_vol=0.5)
    assert detect_regime(ctx) == Regime.UNKNOWN


def test_crisis_overrides_missing_data():
    """High VIX crisis even if BTC data totally absent."""
    ctx = MarketContext(vix=50)
    assert detect_regime(ctx) == Regime.CRISIS


# ================================================================== #
# Custom thresholds
# ================================================================== #
def test_custom_crisis_vix_threshold():
    detector = RegimeDetector(crisis_vix=25.0)
    ctx = MarketContext(vix=27, btc_price=51_000, btc_ma200=50_000,
                        btc_ma200_slope=0.001, btc_realized_vol=0.5)
    assert detector.detect(ctx) == Regime.CRISIS

    # Default (35) wouldn't trigger
    assert detect_regime(ctx) != Regime.CRISIS


def test_custom_trend_vol_cutoff():
    """A more conservative cutoff means more regimes are CHOPPY."""
    detector = RegimeDetector(trend_vol_cutoff=0.4)
    ctx = MarketContext(btc_price=55_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=0.5)
    # 0.5 > 0.4 → CHOPPY under custom; default cutoff 0.6 would give TRENDING
    assert detector.detect(ctx) == Regime.BULL_CHOPPY


def test_custom_near_ma_band():
    """Wider near_ma_band → more situations classified as sideways."""
    detector = RegimeDetector(near_ma_band=0.10)  # 10%
    ctx = MarketContext(btc_price=54_000, btc_ma200=50_000,
                        btc_ma200_slope=0.002, btc_realized_vol=0.35)
    # 8% distance < 10% band → sideways; vol 0.35 < 0.4 cutoff → low vol
    assert detector.detect(ctx) == Regime.SIDEWAYS_LOW_VOL


# ================================================================== #
# Regime enum string roundtrip (used by predicates / JSON)
# ================================================================== #
def test_regime_value_is_string():
    assert Regime.BULL_TRENDING.value == "BULL_TRENDING"


def test_regime_in_predicate_membership():
    """Strategy DSL writes `regime in ["CRISIS", "BEAR_TRENDING"]`. Verify
    string Regime values play nicely in a containment check."""
    forbidden = ["CRISIS", "BEAR_TRENDING"]
    assert Regime.CRISIS.value in forbidden
    assert Regime.BULL_TRENDING.value not in forbidden
