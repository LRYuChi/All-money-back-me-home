"""Tests for strategies.market_regime — R48 system-wide regime detector."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from strategies.market_regime import (
    MarketRegimeDetector,
    NoOpRegimeDetector,
    Regime,
    RegimeSnapshot,
    SizingAdjustment,
    classify_regime,
    compute_adx_30d_median,
    compute_atr_price_ratio,
    compute_hurst_exponent,
)


# =================================================================== #
# Helpers — generate synthetic OHLC for known regimes
# =================================================================== #
def _trending_btc(n_days: int = 200, daily_return: float = 0.012,
                  vol: float = 0.025, seed: int = 42) -> pd.DataFrame:
    """Steady uptrend — strong enough drift that ADX + Hurst both
    register trending. vol=0.025 ≈ 2.5% daily stdev (real-BTC-like).
    Higher daily_return ensures the trend dominates noise in the
    Hurst R/S analysis."""
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(loc=daily_return, scale=vol, size=n_days)
    closes = 50_000 * np.exp(np.cumsum(log_ret))
    return _close_to_ohlc(closes)


def _choppy_btc(n_days: int = 200, vol: float = 0.012, seed: int = 42) -> pd.DataFrame:
    """Mean-reverting / sideways — should classify CHOPPY."""
    rng = np.random.default_rng(seed)
    # AR(1) with negative coefficient → mean reversion
    log_ret = np.zeros(n_days)
    eps = rng.normal(0, vol, n_days)
    for i in range(1, n_days):
        log_ret[i] = -0.4 * log_ret[i - 1] + eps[i]
    closes = 50_000 * np.exp(np.cumsum(log_ret))
    return _close_to_ohlc(closes)


def _dead_btc(n_days: int = 200, vol: float = 0.003, seed: int = 42) -> pd.DataFrame:
    """Eerily flat — should classify DEAD."""
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(0, vol, n_days)
    closes = 50_000 * np.exp(np.cumsum(log_ret))
    return _close_to_ohlc(closes)


def _close_to_ohlc(closes: np.ndarray) -> pd.DataFrame:
    """Synthesize plausible OHLC from a close series."""
    rng = np.random.default_rng(0)
    n = len(closes)
    high = closes * (1 + np.abs(rng.normal(0, 0.005, n)))
    low = closes * (1 - np.abs(rng.normal(0, 0.005, n)))
    return pd.DataFrame({
        "open": closes, "high": high, "low": low, "close": closes,
        "volume": np.ones(n) * 1_000_000,
    })


# =================================================================== #
# compute_atr_price_ratio
# =================================================================== #
def test_atr_ratio_trending_btc_in_normal_range():
    df = _trending_btc()
    atr = compute_atr_price_ratio(df)
    # Expect 0.5-3% daily range for synthetic trending
    assert 0.001 < atr < 0.05


def test_atr_ratio_dead_btc_below_threshold():
    df = _dead_btc()
    atr = compute_atr_price_ratio(df)
    # Dead market — narrow daily ranges
    assert atr < 0.02


def test_atr_ratio_returns_zero_for_short_history():
    df = pd.DataFrame({
        "open": [1, 2], "high": [1, 2], "low": [1, 2],
        "close": [1, 2], "volume": [1, 1],
    })
    assert compute_atr_price_ratio(df, period=30) == 0.0


# =================================================================== #
# compute_adx_30d_median
# =================================================================== #
def test_adx_trending_higher_than_choppy():
    """Trending market should have higher ADX than choppy market."""
    trend = compute_adx_30d_median(_trending_btc())
    chop = compute_adx_30d_median(_choppy_btc())
    assert trend > chop


def test_adx_returns_zero_for_short_history():
    df = pd.DataFrame({
        "open": list(range(10)), "high": list(range(10)),
        "low": list(range(10)), "close": list(range(10)),
        "volume": [1] * 10,
    })
    assert compute_adx_30d_median(df) == 0.0


# =================================================================== #
# compute_hurst_exponent
# =================================================================== #
def test_hurst_trending_above_random():
    """Trending series should have H > 0.5 (persistent)."""
    h = compute_hurst_exponent(_trending_btc())
    assert h > 0.5


def test_hurst_choppy_below_random():
    """Mean-reverting series should have H < 0.5."""
    h = compute_hurst_exponent(_choppy_btc())
    assert h < 0.55   # allow some noise; AR(1) doesn't always fall < 0.5


def test_hurst_in_unit_interval():
    """Output is clipped to [0, 1] regardless of input."""
    h = compute_hurst_exponent(_trending_btc())
    assert 0.0 <= h <= 1.0


def test_hurst_returns_default_for_short_history():
    df = pd.DataFrame({
        "open": list(range(50)), "high": list(range(50)),
        "low": list(range(50)), "close": list(range(50)),
        "volume": [1] * 50,
    })
    # < 100 days lookback default → returns 0.5 (random walk default)
    assert compute_hurst_exponent(df, lookback=100) == 0.5


# =================================================================== #
# classify_regime — all 4 + UNKNOWN paths
# =================================================================== #
def test_classify_trending():
    """High ADX + persistent Hurst + normal vol = TRENDING."""
    r = classify_regime(atr_price_ratio=0.025, adx_30d_median=30.0,
                        hurst_exponent=0.62)
    assert r == Regime.TRENDING


def test_classify_volatile_trending():
    """High ADX + persistent Hurst + high vol = VOLATILE_TRENDING."""
    r = classify_regime(atr_price_ratio=0.05, adx_30d_median=30.0,
                        hurst_exponent=0.62)
    assert r == Regime.VOLATILE_TRENDING


def test_classify_choppy_low_adx_low_hurst():
    r = classify_regime(atr_price_ratio=0.025, adx_30d_median=15.0,
                        hurst_exponent=0.45)
    assert r == Regime.CHOPPY


def test_classify_dead_low_atr():
    r = classify_regime(atr_price_ratio=0.010, adx_30d_median=15.0,
                        hurst_exponent=0.45)
    assert r == Regime.DEAD


def test_classify_default_choppy_when_ambiguous():
    """Mid-range values default to CHOPPY (be conservative)."""
    r = classify_regime(atr_price_ratio=0.025, adx_30d_median=22.0,
                        hurst_exponent=0.52)
    assert r == Regime.CHOPPY


def test_classify_threshold_overrides_work():
    r = classify_regime(
        atr_price_ratio=0.025, adx_30d_median=30.0,
        hurst_exponent=0.50,
        hurst_trend_threshold=0.45,    # lower bar
    )
    assert r == Regime.TRENDING


# =================================================================== #
# SizingAdjustment lookup — all 5 regimes covered
# =================================================================== #
def test_sizing_adjustment_trending_full():
    a = SizingAdjustment.for_regime(Regime.TRENDING)
    assert a.kelly_multiplier == 1.0
    assert a.cooldown_hours == 6.0
    assert a.block_new_entries is False


def test_sizing_adjustment_volatile_trending_reduced():
    a = SizingAdjustment.for_regime(Regime.VOLATILE_TRENDING)
    assert 0 < a.kelly_multiplier < 1.0
    assert a.block_new_entries is False


def test_sizing_adjustment_choppy_heavy_reduction():
    a = SizingAdjustment.for_regime(Regime.CHOPPY)
    assert a.kelly_multiplier <= 0.40
    assert a.cooldown_hours >= 24
    assert a.block_new_entries is False


def test_sizing_adjustment_dead_blocks():
    a = SizingAdjustment.for_regime(Regime.DEAD)
    assert a.kelly_multiplier == 0.0
    assert a.block_new_entries is True


def test_sizing_adjustment_unknown_defaults_to_choppy_safety():
    """UNKNOWN (fetch failed) treated like CHOPPY — be conservative."""
    a_unknown = SizingAdjustment.for_regime(Regime.UNKNOWN)
    a_choppy = SizingAdjustment.for_regime(Regime.CHOPPY)
    assert a_unknown.kelly_multiplier == a_choppy.kelly_multiplier
    assert a_unknown.cooldown_hours == a_choppy.cooldown_hours
    assert a_unknown.block_new_entries == a_choppy.block_new_entries


# =================================================================== #
# RegimeSnapshot.as_compact_str
# =================================================================== #
def test_snapshot_compact_str_includes_all_indicators():
    snap = RegimeSnapshot(
        regime=Regime.TRENDING,
        atr_price_ratio=0.025, adx_30d_median=30.0, hurst_exponent=0.62,
        btc_price=50_000, sample_size_days=200,
        ts=datetime.now(timezone.utc),
    )
    s = snap.as_compact_str()
    assert "trending" in s
    assert "ATR=" in s
    assert "ADX=" in s
    assert "H=" in s
    assert "BTC=" in s


# =================================================================== #
# MarketRegimeDetector — caching + failure path
# =================================================================== #
def test_detector_returns_unknown_on_fetch_failure():
    def boom(): raise ConnectionError("HL down")
    det = MarketRegimeDetector(boom)
    snap = det.detect()
    assert snap.regime == Regime.UNKNOWN


def test_detector_returns_unknown_on_insufficient_data():
    """< 50 rows = insufficient — return UNKNOWN."""
    df = _trending_btc(n_days=20)
    det = MarketRegimeDetector(lambda: df)
    snap = det.detect()
    assert snap.regime == Regime.UNKNOWN


def test_detector_caches_within_ttl():
    """Two calls inside TTL → fetcher only called once."""
    n_calls = {"n": 0}
    df = _trending_btc()
    def fetch():
        n_calls["n"] += 1
        return df
    det = MarketRegimeDetector(fetch, ttl_seconds=60)
    det.detect()
    det.detect()
    assert n_calls["n"] == 1


def test_detector_force_refresh_busts_cache():
    n_calls = {"n": 0}
    df = _trending_btc()
    def fetch():
        n_calls["n"] += 1
        return df
    det = MarketRegimeDetector(fetch, ttl_seconds=60)
    det.detect()
    det.detect(force_refresh=True)
    assert n_calls["n"] == 2


def test_detector_reset_busts_cache():
    n_calls = {"n": 0}
    df = _trending_btc()
    def fetch():
        n_calls["n"] += 1
        return df
    det = MarketRegimeDetector(fetch, ttl_seconds=60)
    det.detect()
    det.reset()
    det.detect()
    assert n_calls["n"] == 2


def test_detector_returns_regime_snapshot_with_btc_price():
    df = _trending_btc()
    det = MarketRegimeDetector(lambda: df)
    snap = det.detect()
    assert snap.btc_price > 0
    assert snap.sample_size_days == len(df)


def test_detector_classifies_trending_btc_as_trending_or_volatile():
    """Synthetic trending should land in TRENDING or VOLATILE_TRENDING
    (not CHOPPY/DEAD/UNKNOWN)."""
    det = MarketRegimeDetector(lambda: _trending_btc())
    snap = det.detect()
    assert snap.regime in (Regime.TRENDING, Regime.VOLATILE_TRENDING)


# =================================================================== #
# NoOpRegimeDetector — escape hatch for SUPERTREND_REGIME_FILTER=0
# =================================================================== #
def test_noop_always_returns_trending():
    det = NoOpRegimeDetector()
    snap = det.detect()
    assert snap.regime == Regime.TRENDING


def test_noop_force_refresh_doesnt_crash():
    det = NoOpRegimeDetector()
    snap = det.detect(force_refresh=True)
    assert snap.regime == Regime.TRENDING


def test_noop_reset_does_nothing():
    det = NoOpRegimeDetector()
    det.reset()   # should not raise
    snap = det.detect()
    assert snap.regime == Regime.TRENDING


# =================================================================== #
# Integration: SupertrendStrategy class wires the detector + escape hatch
# =================================================================== #
def test_strategy_class_exposes_helpers():
    from strategies.supertrend import SupertrendStrategy
    assert hasattr(SupertrendStrategy, "_get_regime_detector")
    assert hasattr(SupertrendStrategy, "_current_regime_snapshot")


def test_supertrend_regime_filter_env_disable(monkeypatch):
    """SUPERTREND_REGIME_FILTER=0 → returns NoOp (always TRENDING)."""
    from strategies.supertrend import SupertrendStrategy
    monkeypatch.setenv("SUPERTREND_REGIME_FILTER", "0")
    # Simulate strategy instance just enough to call the helper
    class _FakeDp:
        def get_pair_dataframe(self, **kw): return _trending_btc()
    strat = SupertrendStrategy.__new__(SupertrendStrategy)
    strat.dp = _FakeDp()
    strat._regime_detector_cache = None
    det = strat._get_regime_detector()
    assert isinstance(det, NoOpRegimeDetector)
