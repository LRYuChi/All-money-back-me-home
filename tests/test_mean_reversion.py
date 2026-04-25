"""Tests for strategies/mean_reversion.py — R67.

Covers:
  - Pure helpers (BB calc, entry/exit conditions)
  - populate_indicators column emission
  - populate_entry_trend edge-trigger + master switch
  - populate_exit_trend midline cross
  - confirm_trade_entry regime gate
  - custom_stoploss ATR formula
  - custom_exit time stop + regime invalidation
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from strategies.mean_reversion import (
    MeanReversionStrategy,
    compute_bollinger,
    is_long_entry,
    is_mean_reverted,
    is_short_entry,
)


# =================================================================== #
# Pure helpers
# =================================================================== #
def test_bollinger_returns_three_bands():
    closes = pd.Series([10.0] * 30 + [12.0, 8.0, 11.0, 9.0, 13.0])
    upper, mid, lower = compute_bollinger(closes, period=20, sigma=2.0)
    assert len(upper) == len(closes)
    # Last row: bands should be valid floats, upper > mid > lower
    assert upper.iloc[-1] > mid.iloc[-1] > lower.iloc[-1]


def test_bollinger_widens_with_volatility():
    flat = pd.Series([10.0] * 30)
    noisy = pd.Series([10.0] * 15 + [10.0 + i * 0.5 for i in range(15)])
    _, _, lo_flat = compute_bollinger(flat, 20, 2.0)
    _, _, lo_noisy = compute_bollinger(noisy, 20, 2.0)
    # Noisy has wider band → lower band is further below mid
    assert (10 - lo_noisy.iloc[-1]) > (10 - lo_flat.iloc[-1])


def test_long_entry_requires_both_conditions():
    # Below lower band AND RSI < 30
    assert is_long_entry(close=95, bb_lower=100, rsi=25) is True
    # Below band but RSI not low enough → reject
    assert is_long_entry(close=95, bb_lower=100, rsi=45) is False
    # RSI low but close above band → reject
    assert is_long_entry(close=105, bb_lower=100, rsi=25) is False


def test_short_entry_requires_both_conditions():
    assert is_short_entry(close=105, bb_upper=100, rsi=75) is True
    assert is_short_entry(close=105, bb_upper=100, rsi=55) is False
    assert is_short_entry(close=95, bb_upper=100, rsi=75) is False


def test_entry_safe_against_nans():
    """NaN inputs (warmup period) must not raise nor fire."""
    assert is_long_entry(np.nan, 100, 25) is False
    assert is_short_entry(105, np.nan, 75) is False


def test_mean_reverted_long_returns_to_mid():
    # Long: reverted when close is back >= mid (-0.1% slack)
    assert is_mean_reverted(100, 100, "long") is True
    assert is_mean_reverted(101, 100, "long") is True
    assert is_mean_reverted(95, 100, "long") is False


def test_mean_reverted_short_returns_to_mid():
    assert is_mean_reverted(100, 100, "short") is True
    assert is_mean_reverted(99, 100, "short") is True
    assert is_mean_reverted(105, 100, "short") is False


# =================================================================== #
# populate_indicators
# =================================================================== #
def _mk_close_series(n: int = 80, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 0.5, n))
    highs = closes + rng.uniform(0.1, 1.0, n)
    lows = closes - rng.uniform(0.1, 1.0, n)
    return pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.uniform(100, 1000, n),
    })


def test_populate_indicators_emits_required_columns(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.bb_period = 20
    s.bb_sigma = 2.0
    s.rsi_period = 14
    s.atr_period = 14
    df = _mk_close_series(80)
    out = s.populate_indicators(df, {"pair": "BTC/USDT:USDT"})
    for col in ("bb_upper", "bb_mid", "bb_lower", "rsi", "atr"):
        assert col in out.columns, f"missing {col}"
    # Last row must have non-NaN values (warmup complete)
    assert not pd.isna(out["bb_mid"].iloc[-1])
    assert not pd.isna(out["rsi"].iloc[-1])


# =================================================================== #
# populate_entry_trend — master switch + edge trigger
# =================================================================== #
def _strategy_with_indicators(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.bb_period = 20
    s.bb_sigma = 2.0
    s.rsi_period = 14
    s.atr_period = 14
    s.rsi_oversold = 30.0
    s.rsi_overbought = 70.0
    return s


def test_entry_trend_master_switch_off(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "0")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    df = _mk_close_series(80)
    df = MeanReversionStrategy.populate_indicators(s, df, {})
    out = s.populate_entry_trend(df, {})
    assert "enter_long" not in out.columns or out["enter_long"].sum() == 0
    assert "enter_short" not in out.columns or out["enter_short"].sum() == 0


def test_entry_trend_fires_on_oversold_edge(monkeypatch):
    s = _strategy_with_indicators(monkeypatch)
    # 50 flat (std=0 → BB collapses to mid; close == lower → oversold False)
    # then 1 sudden drop → BB widens but close drops further → first oversold
    closes = [100] * 50 + [70]
    df = pd.DataFrame({"open": closes, "high": [c + 0.5 for c in closes],
                       "low": [c - 0.5 for c in closes], "close": closes,
                       "volume": [500] * len(closes)})
    df = s.populate_indicators(df, {})
    # Sanity: last close clearly below bb_lower
    assert df["close"].iloc[-1] < df["bb_lower"].iloc[-1]
    out = s.populate_entry_trend(df, {"pair": "X"})
    # First (and only) candle to satisfy oversold → edge fires
    assert out["enter_long"].iloc[-1] == 1
    assert out["enter_tag"].iloc[-1] == "mr_long_oversold"


def test_entry_trend_does_not_fire_on_sustained_oversold(monkeypatch):
    """Edge trigger: if oversold was True last candle, no fire this candle."""
    s = _strategy_with_indicators(monkeypatch)
    # 5 consecutive oversold candles → only the FIRST can fire
    closes = [100] * 30 + [80, 78, 76, 74, 72]
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes],
                       "low": [c - 1 for c in closes], "close": closes,
                       "volume": [500] * len(closes)})
    df = s.populate_indicators(df, {})
    out = s.populate_entry_trend(df, {})
    # Only the first oversold candle fires; subsequent are suppressed
    fires = (out["enter_long"] == 1).sum() if "enter_long" in out else 0
    assert fires <= 1


def test_entry_trend_fires_on_overbought_edge(monkeypatch):
    s = _strategy_with_indicators(monkeypatch)
    closes = [100] * 30 + [101, 102, 103, 104, 105, 110, 120]
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes],
                       "low": [c - 1 for c in closes], "close": closes,
                       "volume": [500] * len(closes)})
    df = s.populate_indicators(df, {})
    out = s.populate_entry_trend(df, {})
    # Some overbought candle should fire short
    if "enter_short" in out.columns:
        assert out["enter_short"].sum() >= 1


# =================================================================== #
# populate_exit_trend — midline cross
# =================================================================== #
def test_exit_long_when_close_reaches_mid(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.bb_period = 20
    s.bb_sigma = 2.0
    s.rsi_period = 14
    s.atr_period = 14
    closes = [100] * 30 + [99, 98, 97, 99, 100, 101]
    df = pd.DataFrame({"open": closes, "high": [c + 1 for c in closes],
                       "low": [c - 1 for c in closes], "close": closes,
                       "volume": [500] * len(closes)})
    df = s.populate_indicators(df, {})
    out = s.populate_exit_trend(df, {})
    # Last close (101) is above mid (~100) → exit_long set
    assert out["exit_long"].iloc[-1] == 1


# =================================================================== #
# confirm_trade_entry — regime gate
# =================================================================== #
@pytest.fixture
def strategy_with_regime():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.dp = MagicMock()
    s.kelly_fraction = 0.05
    s._regime_detector_cache = None
    return s


def test_regime_gate_passes_when_choppy(strategy_with_regime, monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    monkeypatch.setenv("MR_REGIME_GATE", "1")
    fake_det = MagicMock()
    fake_snap = MagicMock()
    from strategies.market_regime import Regime
    fake_snap.regime = Regime.CHOPPY
    fake_det.detect.return_value = fake_snap
    with patch.object(strategy_with_regime, "_get_regime_detector",
                      return_value=fake_det):
        allow, reason = strategy_with_regime._regime_gate_passes()
    assert allow is True
    assert "choppy" in reason.lower()


def test_regime_gate_blocks_when_trending(strategy_with_regime, monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    monkeypatch.setenv("MR_REGIME_GATE", "1")
    fake_det = MagicMock()
    fake_snap = MagicMock()
    from strategies.market_regime import Regime
    fake_snap.regime = Regime.TRENDING
    fake_det.detect.return_value = fake_snap
    with patch.object(strategy_with_regime, "_get_regime_detector",
                      return_value=fake_det):
        allow, reason = strategy_with_regime._regime_gate_passes()
    assert allow is False


def test_regime_gate_fails_open_when_detector_unavailable(strategy_with_regime,
                                                           monkeypatch):
    monkeypatch.setenv("MR_REGIME_GATE", "1")
    with patch.object(strategy_with_regime, "_get_regime_detector",
                      return_value=None):
        allow, reason = strategy_with_regime._regime_gate_passes()
    assert allow is True
    assert "unavailable" in reason


def test_regime_gate_off_via_env(strategy_with_regime, monkeypatch):
    monkeypatch.setenv("MR_REGIME_GATE", "0")
    allow, reason = strategy_with_regime._regime_gate_passes()
    assert allow is True
    assert reason == "gate_off"


def test_confirm_entry_returns_false_when_master_off(strategy_with_regime,
                                                       monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "0")
    res = strategy_with_regime.confirm_trade_entry(
        pair="BTC/USDT:USDT", order_type="limit", amount=1.0,
        rate=50000, time_in_force="GTC",
        current_time=datetime.now(timezone.utc),
        entry_tag="mr_long_oversold", side="long",
    )
    assert res is False


def test_confirm_entry_returns_false_when_regime_blocks(strategy_with_regime,
                                                         monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    monkeypatch.setenv("MR_REGIME_GATE", "1")
    fake_det = MagicMock()
    fake_snap = MagicMock()
    from strategies.market_regime import Regime
    fake_snap.regime = Regime.DEAD
    fake_det.detect.return_value = fake_snap
    with patch.object(strategy_with_regime, "_get_regime_detector",
                      return_value=fake_det):
        res = strategy_with_regime.confirm_trade_entry(
            pair="X", order_type="limit", amount=1.0, rate=100,
            time_in_force="GTC",
            current_time=datetime.now(timezone.utc),
            entry_tag="mr_long_oversold", side="long",
        )
    assert res is False


# =================================================================== #
# custom_stoploss — ATR formula
# =================================================================== #
def test_custom_stoploss_returns_none_when_no_atr_data():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.atr_stop_mult = 1.5
    s.timeframe = "15m"
    s.dp = MagicMock()
    s.dp.get_analyzed_dataframe.return_value = (pd.DataFrame(), None)
    fake_trade = MagicMock(); fake_trade.open_rate = 100
    sl = s.custom_stoploss("X", fake_trade,
                           datetime.now(timezone.utc), 100, 0.0)
    assert sl is None


def test_custom_stoploss_caps_at_minus_10_pct():
    """Crazy ATR spike (e.g., flash crash) cannot widen SL below -10%."""
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.atr_stop_mult = 1.5
    s.timeframe = "15m"
    s.dp = MagicMock()
    # ATR = 50% of price → stop = 1.5 × 50% = 75% (way too wide)
    df = pd.DataFrame({"atr": [50.0]})
    s.dp.get_analyzed_dataframe.return_value = (df, None)
    fake_trade = MagicMock(); fake_trade.open_rate = 100
    sl = s.custom_stoploss("X", fake_trade,
                           datetime.now(timezone.utc), 100, 0.0)
    assert sl >= -0.10   # capped


def test_custom_stoploss_computes_atr_based_pct():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.atr_stop_mult = 1.5
    s.timeframe = "15m"
    s.dp = MagicMock()
    # Entry 100, ATR 2 → stop = 1.5 × 2 / 100 = 3%
    df = pd.DataFrame({"atr": [2.0]})
    s.dp.get_analyzed_dataframe.return_value = (df, None)
    fake_trade = MagicMock(); fake_trade.open_rate = 100
    sl = s.custom_stoploss("X", fake_trade,
                           datetime.now(timezone.utc), 100, 0.0)
    assert abs(sl - (-0.03)) < 0.001


# =================================================================== #
# custom_exit — time stop + regime invalidation
# =================================================================== #
def test_custom_exit_time_stop_after_24h():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.time_stop_hours = 24
    fake_trade = MagicMock()
    fake_trade.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=25)
    res = s.custom_exit("X", fake_trade,
                        datetime.now(timezone.utc), 100, 0.01)
    assert res is not None and "time_stop" in res


def test_custom_exit_no_action_within_window():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.time_stop_hours = 24
    s._regime_detector_cache = None
    fake_trade = MagicMock()
    fake_trade.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=2)
    with patch.object(s, "_get_regime_detector", return_value=None):
        res = s.custom_exit("X", fake_trade,
                            datetime.now(timezone.utc), 100, 0.01)
    assert res is None


def test_custom_exit_invalidates_on_trending_regime(monkeypatch):
    monkeypatch.setenv("MR_REGIME_GATE", "1")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.time_stop_hours = 24
    s._regime_detector_cache = None
    fake_trade = MagicMock()
    fake_trade.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=1)
    fake_det = MagicMock()
    fake_snap = MagicMock()
    from strategies.market_regime import Regime
    fake_snap.regime = Regime.TRENDING
    fake_det.detect.return_value = fake_snap
    with patch.object(s, "_get_regime_detector", return_value=fake_det):
        res = s.custom_exit("X", fake_trade,
                            datetime.now(timezone.utc), 100, 0.01)
    assert res is not None and "regime_invalidated" in res


# =================================================================== #
# custom_stake_amount
# =================================================================== #
def test_custom_stake_returns_zero_when_master_off(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "0")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.kelly_fraction = 0.05
    s.wallets = MagicMock()
    s.wallets.get_total_stake_amount.return_value = 1000
    res = s.custom_stake_amount(
        current_time=datetime.now(timezone.utc), current_rate=100,
        proposed_stake=50, min_stake=10, max_stake=500, leverage=1,
        entry_tag="mr_long_oversold", side="long",
    )
    assert res == 0.0


def test_custom_stake_uses_kelly_fraction(monkeypatch):
    monkeypatch.setenv("MR_ENABLED", "1")
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.kelly_fraction = 0.05
    s.wallets = MagicMock()
    s.wallets.get_total_stake_amount.return_value = 1000
    res = s.custom_stake_amount(
        current_time=datetime.now(timezone.utc), current_rate=100,
        proposed_stake=999, min_stake=10, max_stake=500, leverage=1,
        entry_tag="mr_long_oversold", side="long",
    )
    # 1000 × 0.05 = 50, capped by max_stake=500
    assert res == 50.0
