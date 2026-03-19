"""Tests for the confidence engine — validates regime determination and safety mechanisms."""


from market_monitor.confidence_engine import (
    GlobalConfidenceEngine,
    EventOverlay,
    z_score,
    z_to_score,
)
from datetime import datetime


# =============================================
# Z-Score utilities
# =============================================

def test_z_score_normal():
    import pandas as pd
    series = pd.Series([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])
    z = z_score(series, 20)
    assert z > 0  # 20 is above the mean


def test_z_score_insufficient_data():
    import pandas as pd
    series = pd.Series([1, 2])
    assert z_score(series, 2) == 0.0


def test_z_to_score_negative_bullish():
    # Lower is better (e.g., VIX): z=-2 should give high score
    assert z_to_score(-2, "negative") > 0.8
    assert z_to_score(2, "negative") < 0.2


def test_z_to_score_positive_bullish():
    # Higher is better (e.g., M2): z=+2 should give high score
    assert z_to_score(2, "positive") > 0.8
    assert z_to_score(-2, "positive") < 0.2


def test_z_to_score_clamped():
    assert z_to_score(100, "positive") == 1.0
    assert z_to_score(-100, "positive") == 0.0


# =============================================
# Event Overlay
# =============================================

def test_event_overlay_normal_day():
    overlay = EventOverlay()
    # A day far from any event
    dt = datetime(2026, 7, 15)
    assert overlay.get_multiplier(dt) == 1.0


def test_event_overlay_fomc():
    overlay = EventOverlay()
    # FOMC day
    dt = datetime(2026, 5, 6)
    mult = overlay.get_multiplier(dt)
    assert mult <= 0.5  # Should reduce confidence significantly


def test_event_overlay_cpi():
    overlay = EventOverlay()
    dt = datetime(2026, 4, 14)
    mult = overlay.get_multiplier(dt)
    assert mult <= 0.7


def test_event_overlay_2027_works():
    overlay = EventOverlay()
    # 2027 FOMC date
    dt = datetime(2027, 1, 27)
    mult = overlay.get_multiplier(dt)
    assert mult <= 0.5


# =============================================
# Regime determination
# =============================================

def test_regime_aggressive():
    assert GlobalConfidenceEngine._score_to_regime(0.85) == "AGGRESSIVE"


def test_regime_normal():
    assert GlobalConfidenceEngine._score_to_regime(0.65) == "NORMAL"


def test_regime_cautious():
    assert GlobalConfidenceEngine._score_to_regime(0.45) == "CAUTIOUS"


def test_regime_defensive():
    assert GlobalConfidenceEngine._score_to_regime(0.25) == "DEFENSIVE"


def test_regime_hibernate():
    assert GlobalConfidenceEngine._score_to_regime(0.15) == "HIBERNATE"


# =============================================
# Guidance
# =============================================

def test_guidance_hibernate_blocks():
    g = GlobalConfidenceEngine._regime_guidance("HIBERNATE")
    assert g["position_pct"] == 0
    assert g["leverage"] == 0


def test_guidance_aggressive_full():
    g = GlobalConfidenceEngine._regime_guidance("AGGRESSIVE")
    assert g["position_pct"] == 100
    assert g["leverage"] == 3.0


# =============================================
# Data blackout safety
# =============================================

def test_data_blackout_detection():
    """When 70%+ factors return default 0.5, engine should degrade."""
    engine = GlobalConfidenceEngine()

    # Monkey-patch all sandboxes to return 0.5 (simulating total API failure)
    engine.macro.calculate = lambda: {"nfci": 0.5, "yield_10y": 0.5, "dxy": 0.5, "m2_growth": 0.5, "oil": 0.5}
    engine.sentiment.calculate = lambda: {"vix": 0.5, "fear_greed": 0.5, "gs_rai": 0.5}
    engine.capital.calculate = lambda: {"btc_d": 0.5, "stablecoin": 0.5, "spy_btc_corr": 0.5}
    engine.haven.calculate = lambda: {"gold_trend": 0.5, "gold_oil": 0.5, "gold_btc_corr": 0.5}

    result = engine.calculate()

    # With data blackout, score should be forced below 0.4 (CAUTIOUS or lower)
    assert result["score"] <= 0.35, f"Expected <= 0.35 during blackout, got {result['score']}"
    assert result["regime"] in ("DEFENSIVE", "HIBERNATE", "CAUTIOUS")
