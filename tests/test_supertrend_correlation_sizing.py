"""Tests for R58 — correlation/rotation wiring in custom_stake_amount.

Exercises the standalone helpers:
  _gather_closes_for_correlation
  _correlation_stake_multiplier

The full custom_stake_amount path requires Freqtrade scaffolding (Trade
proxy, Wallets, etc) so we test the helpers directly with mocked dp.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


def _synthetic_closes(n: int = 35, start: float = 100.0,
                      drift: float = 0.005, vol: float = 0.02,
                      seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n - 1)
    closes = [start]
    for r in rets:
        closes.append(closes[-1] * (1 + r))
    return closes


@pytest.fixture
def strategy():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.dp = MagicMock()
    return s


def _make_dp_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


# =================================================================== #
# _gather_closes_for_correlation
# =================================================================== #
def test_gather_includes_intended_and_btc(strategy):
    btc = _synthetic_closes(seed=1)
    eth = _synthetic_closes(seed=2)
    strategy.dp.get_pair_dataframe.side_effect = lambda p, tf: (
        _make_dp_df(btc) if "BTC" in p else _make_dp_df(eth)
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        closes = strategy._gather_closes_for_correlation("ETH/USDT:USDT")
    assert "ETH/USDT:USDT" in closes
    assert "BTC/USDT:USDT" in closes


def test_gather_includes_open_trade_pairs(strategy):
    strategy.dp.get_pair_dataframe.return_value = _make_dp_df(_synthetic_closes())
    fake_trade = MagicMock()
    fake_trade.pair = "AVAX/USDT:USDT"
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[fake_trade],
    ):
        closes = strategy._gather_closes_for_correlation("ETH/USDT:USDT")
    assert "AVAX/USDT:USDT" in closes


def test_gather_skips_pairs_with_short_history(strategy):
    """Pair with < lookback+1 bars must be excluded."""
    long_series = _synthetic_closes(n=35)
    short_series = _synthetic_closes(n=10)
    strategy.dp.get_pair_dataframe.side_effect = lambda p, tf: (
        _make_dp_df(long_series) if "BTC" in p else _make_dp_df(short_series)
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        closes = strategy._gather_closes_for_correlation("ETH/USDT:USDT")
    assert "BTC/USDT:USDT" in closes
    assert "ETH/USDT:USDT" not in closes


def test_gather_silent_on_dp_failure(strategy):
    strategy.dp.get_pair_dataframe.side_effect = RuntimeError("boom")
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        closes = strategy._gather_closes_for_correlation("ETH/USDT:USDT")
    assert closes == {}


# =================================================================== #
# _correlation_stake_multiplier — escape hatch
# =================================================================== #
def test_corr_mult_off_by_default(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_CORRELATION_FILTER", raising=False)
    mult, reason = strategy._correlation_stake_multiplier("ETH/USDT:USDT")
    assert mult == 1.0
    assert reason is None
    # dp should NOT be queried when env unset
    strategy.dp.get_pair_dataframe.assert_not_called()


def test_corr_mult_off_explicit_zero(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "0")
    mult, reason = strategy._correlation_stake_multiplier("ETH/USDT:USDT")
    assert mult == 1.0
    assert reason is None


# =================================================================== #
# _correlation_stake_multiplier — enabled, rotation phase
# =================================================================== #
def test_corr_mult_returns_alt_season_boost(strategy, monkeypatch):
    """ALT_SEASON: alts get 1.2×, BTC/ETH get 0.7×."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    # BTC flat, alt up 30% → ALT_SEASON
    btc = [100.0] * 35
    alt = _synthetic_closes(n=35, drift=0.01, vol=0.005, seed=10)
    strategy.dp.get_pair_dataframe.side_effect = lambda p, tf: (
        _make_dp_df(btc) if "BTC" in p else _make_dp_df(alt)
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        mult, reason = strategy._correlation_stake_multiplier("AVAX/USDT:USDT")
    assert reason is None
    assert mult == 1.2


def test_corr_mult_returns_btc_strong_alt_penalty(strategy, monkeypatch):
    """BTC_STRONG: alts get 0.7×."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    btc = _synthetic_closes(n=35, drift=0.01, vol=0.005, seed=20)
    alt = [100.0] * 35
    strategy.dp.get_pair_dataframe.side_effect = lambda p, tf: (
        _make_dp_df(btc) if "BTC" in p else _make_dp_df(alt)
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        mult, reason = strategy._correlation_stake_multiplier("AVAX/USDT:USDT")
    assert reason is None
    assert mult == 0.7


def test_corr_mult_consolidation_unchanged(strategy, monkeypatch):
    """Both BTC and alt drift the same → CONSOLIDATION → 1.0×."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    btc = _synthetic_closes(n=35, drift=0.001, vol=0.005, seed=30)
    alt = _synthetic_closes(n=35, drift=0.001, vol=0.005, seed=31)
    strategy.dp.get_pair_dataframe.side_effect = lambda p, tf: (
        _make_dp_df(btc) if "BTC" in p else _make_dp_df(alt)
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        mult, reason = strategy._correlation_stake_multiplier("AVAX/USDT:USDT")
    assert reason is None
    assert mult == 1.0


# =================================================================== #
# _correlation_stake_multiplier — concentration block
# =================================================================== #
def test_corr_mult_blocks_on_concentration(strategy, monkeypatch):
    """Two open trades + intended pair, all moving together → block."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    # Single shared series → mean ρ ≈ 1.0
    same = _synthetic_closes(n=35, drift=0.005, vol=0.02, seed=42)
    strategy.dp.get_pair_dataframe.return_value = _make_dp_df(same)
    t1, t2 = MagicMock(), MagicMock()
    t1.pair, t2.pair = "ETH/USDT:USDT", "AVAX/USDT:USDT"
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[t1, t2],
    ):
        mult, reason = strategy._correlation_stake_multiplier("SOL/USDT:USDT")
    assert mult == 0.0
    assert reason is not None
    assert "concentration" in reason


def test_corr_mult_does_not_block_with_zero_open(strategy, monkeypatch):
    """No open trades → can't be 'concentrated' even if ρ high."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    same = _synthetic_closes(n=35, drift=0.005, vol=0.02, seed=42)
    strategy.dp.get_pair_dataframe.return_value = _make_dp_df(same)
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        mult, reason = strategy._correlation_stake_multiplier("ETH/USDT:USDT")
    # Phase will be CONSOLIDATION (both same series) → 1.0
    assert reason is None
    assert mult > 0.0


def test_corr_mult_does_not_block_with_one_open(strategy, monkeypatch):
    """Single open trade is not yet 'concentrated' — needs ≥2 to gate."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    same = _synthetic_closes(n=35, drift=0.005, vol=0.02, seed=42)
    strategy.dp.get_pair_dataframe.return_value = _make_dp_df(same)
    t1 = MagicMock()
    t1.pair = "ETH/USDT:USDT"
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[t1],
    ):
        mult, reason = strategy._correlation_stake_multiplier("AVAX/USDT:USDT")
    assert reason is None
    assert mult > 0.0


def test_corr_mult_silent_on_no_data(strategy, monkeypatch):
    """If dp returns nothing, fail-open with neutral 1.0×."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    strategy.dp.get_pair_dataframe.return_value = pd.DataFrame()
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        mult, reason = strategy._correlation_stake_multiplier("ETH/USDT:USDT")
    assert mult == 1.0
    assert reason is None


def test_corr_mult_silent_on_snapshot_failure(strategy, monkeypatch):
    """Any internal exception in build_snapshot fails open."""
    monkeypatch.setenv("SUPERTREND_CORRELATION_FILTER", "1")
    strategy.dp.get_pair_dataframe.return_value = _make_dp_df(
        _synthetic_closes(n=35),
    )
    with patch(
        "freqtrade.persistence.Trade.get_trades_proxy",
        return_value=[],
    ):
        with patch(
            "strategies.supertrend._corr_build_snapshot",
            side_effect=RuntimeError("internal"),
        ):
            mult, reason = strategy._correlation_stake_multiplier(
                "ETH/USDT:USDT",
            )
    assert mult == 1.0
    assert reason is None
