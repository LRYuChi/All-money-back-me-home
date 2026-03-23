"""Volty Expan Close Strategy — port from TradingView Pine Script v5.

Volatility expansion breakout strategy:
- Calculates ATR-based threshold: SMA(TR, 5) × 0.75
- Places stop-entry orders above and below current close
- Long entry: close + threshold (breakout up)
- Short entry: close - threshold (breakout down)

Every bar updates both stop-entry levels, so the strategy is always
ready to enter in whichever direction price breaks out.

In Freqtrade (no stop-entry orders), we simulate by checking if
the current bar's high/low crossed the previous bar's threshold levels.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.strategy import IStrategy
from pandas import DataFrame


class VoltyExpanStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    startup_candle_count = 50

    stoploss = -0.05
    trailing_stop = False
    use_custom_stoploss = False

    can_short = True
    trading_mode = "futures"
    margin_mode = "isolated"

    # Parameters matching Pine Script defaults
    ve_length = 5
    ve_num_atrs = 0.75

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # True Range → SMA(TR, length) × numATRs
        dataframe["tr"] = ta.TRANGE(dataframe)
        dataframe["atrs"] = ta.SMA(dataframe["tr"], timeperiod=self.ve_length) * self.ve_num_atrs

        # Stop-entry levels (based on previous bar's close + threshold)
        dataframe["long_stop_entry"] = dataframe["close"].shift(1) + dataframe["atrs"].shift(1)
        dataframe["short_stop_entry"] = dataframe["close"].shift(1) - dataframe["atrs"].shift(1)

        # Simulate stop-entry: triggered when price reaches the level
        # Long: current high >= long_stop_entry (price broke upward)
        dataframe["ve_buy"] = dataframe["high"] >= dataframe["long_stop_entry"]
        # Short: current low <= short_stop_entry (price broke downward)
        dataframe["ve_sell"] = dataframe["low"] <= dataframe["short_stop_entry"]

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # When both trigger on same bar, skip (ambiguous)
        both = dataframe["ve_buy"] & dataframe["ve_sell"]

        dataframe.loc[dataframe["ve_buy"] & ~both, "enter_long"] = 1
        dataframe.loc[dataframe["ve_sell"] & ~both, "enter_short"] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit on opposite signal
        both = dataframe["ve_buy"] & dataframe["ve_sell"]
        dataframe.loc[dataframe["ve_sell"] & ~both, "exit_long"] = 1
        dataframe.loc[dataframe["ve_buy"] & ~both, "exit_short"] = 1
        return dataframe
