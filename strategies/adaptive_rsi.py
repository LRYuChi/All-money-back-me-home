"""Adaptive RSI Strategy for crypto futures (inspired by ai-trader's AdaptiveRSI).

The RSI period dynamically adjusts based on market volatility:
- High volatility → shorter RSI period (faster response)
- Low volatility → longer RSI period (smoother signals)

Designed for USDT perpetual futures on OKX via Freqtrade.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy
from pandas import DataFrame

logger = logging.getLogger(__name__)


class AdaptiveRSI(IStrategy):
    """Adaptive RSI strategy with volatility-based period adjustment."""

    INTERFACE_VERSION = 3

    # Strategy settings
    timeframe = "1h"
    can_short = True
    stoploss = -0.03
    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    # Need enough candles for SMA(100) + rolling(100)
    startup_candle_count = 200

    # Futures settings
    leverage_default = 3.0

    # Hyperparameters
    rsi_min_period = IntParameter(6, 14, default=8, space="buy")
    rsi_max_period = IntParameter(20, 40, default=28, space="buy")
    atr_period = IntParameter(10, 20, default=14, space="buy")
    rsi_oversold = DecimalParameter(20, 45, default=40, space="buy")
    rsi_overbought = DecimalParameter(55, 80, default=60, space="sell")
    trend_sma_period = IntParameter(50, 200, default=100, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate adaptive RSI and supporting indicators."""
        # ATR for volatility measurement
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period.value)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"] * 100

        # Volatility percentile (rolling 100 bars)
        dataframe["vol_pctile"] = (
            dataframe["atr_pct"]
            .rolling(100)
            .apply(lambda x: np.percentile(x, (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100))
        )

        # Adaptive RSI period: high vol → short period, low vol → long period
        min_p = self.rsi_min_period.value
        max_p = self.rsi_max_period.value
        dataframe["adaptive_period"] = (
            max_p - (dataframe["vol_pctile"].fillna(50) / 100 * (max_p - min_p))
        ).clip(min_p, max_p).astype(int)

        # Calculate RSI with the median adaptive period (Freqtrade requires fixed-length indicators)
        median_period = int(dataframe["adaptive_period"].dropna().median()) if len(dataframe) > 0 else 14
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=median_period)

        # Trend filter
        dataframe["sma_trend"] = ta.SMA(dataframe, timeperiod=self.trend_sma_period.value)

        # Bollinger Bands for mean reversion confirmation
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_upper"] = bb["upperband"]
        dataframe["bb_lower"] = bb["lowerband"]
        dataframe["bb_mid"] = bb["middleband"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Define entry conditions."""
        # Long entry: RSI oversold
        dataframe.loc[
            (
                (dataframe["rsi"] < self.rsi_oversold.value)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # Short entry: RSI overbought
        dataframe.loc[
            (
                (dataframe["rsi"] > self.rsi_overbought.value)
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Define exit conditions."""
        # Exit long: RSI crosses above overbought or price crosses below trend
        dataframe.loc[
            (
                (dataframe["rsi"] > self.rsi_overbought.value)
                | (dataframe["close"] < dataframe["sma_trend"])
            ),
            "exit_long",
        ] = 1

        # Exit short: RSI crosses below oversold or price crosses above trend
        dataframe.loc[
            (
                (dataframe["rsi"] < self.rsi_oversold.value)
                | (dataframe["close"] > dataframe["sma_trend"])
            ),
            "exit_short",
        ] = 1

        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Return leverage for the trade, clamped to our max."""
        return min(self.leverage_default, 5.0)
