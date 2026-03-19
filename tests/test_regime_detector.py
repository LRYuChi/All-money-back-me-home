"""Tests for the RegimeDetector."""

from agent.regime_detector import RegimeDetector


def test_classify_high_volatility():
    d = RegimeDetector()
    regime, conf = d._classify(conf_score=0.5, conf_regime="CAUTIOUS",
                                btc_change=-8, btc_rsi=30, vix=35, fg=20, btc_env=0.3)
    assert regime == "HIGH_VOLATILITY"
    assert conf > 0.7


def test_classify_trending_bear():
    d = RegimeDetector()
    regime, conf = d._classify(conf_score=0.15, conf_regime="HIBERNATE",
                                btc_change=-2, btc_rsi=35, vix=25, fg=15, btc_env=0.3)
    assert regime == "TRENDING_BEAR"


def test_classify_trending_bull():
    d = RegimeDetector()
    regime, conf = d._classify(conf_score=0.75, conf_regime="AGGRESSIVE",
                                btc_change=3, btc_rsi=65, vix=15, fg=70, btc_env=0.7)
    assert regime == "TRENDING_BULL"


def test_classify_accumulation():
    d = RegimeDetector()
    regime, conf = d._classify(conf_score=0.45, conf_regime="CAUTIOUS",
                                btc_change=0.5, btc_rsi=45, vix=15, fg=30, btc_env=0.5)
    assert regime == "ACCUMULATION"


def test_classify_ranging_default():
    d = RegimeDetector()
    regime, conf = d._classify(conf_score=0.5, conf_regime="CAUTIOUS",
                                btc_change=1, btc_rsi=50, vix=20, fg=50, btc_env=0.5)
    assert regime == "RANGING"


def test_guidance_exists_for_all():
    d = RegimeDetector()
    for regime in ["TRENDING_BULL", "TRENDING_BEAR", "HIGH_VOLATILITY", "ACCUMULATION", "RANGING", "UNKNOWN"]:
        g = d._regime_guidance(regime)
        assert "strategy" in g
        assert "leverage_cap" in g
        assert "risk_level" in g
        assert g["leverage_cap"] <= 5.0


def test_high_vol_priority_over_bear():
    """HIGH_VOLATILITY should take priority over TRENDING_BEAR."""
    d = RegimeDetector()
    regime, _ = d._classify(conf_score=0.1, conf_regime="HIBERNATE",
                             btc_change=-12, btc_rsi=20, vix=40, fg=10, btc_env=0.1)
    assert regime == "HIGH_VOLATILITY"  # VIX > 30 takes priority
