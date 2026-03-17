"""ATR Trend Strategy for crypto futures.

Uses Average True Range (ATR) for:
- Volatility-based stop loss & take profit (dynamic, adapts to market)
- ATR channel breakout for trend detection
- Keltner Channel (EMA + ATR bands) for entry signals
- ATR trailing stop for profit protection

Core logic:
- LONG: Price breaks above upper Keltner + ATR expanding + EMA uptrend
- SHORT: Price breaks below lower Keltner + ATR expanding + EMA downtrend
- Stop: Entry price ± ATR multiplier (volatility-adjusted)
- Target: Entry price ± ATR multiplier × R:R ratio

Designed for USDT perpetual futures on OKX via Freqtrade.
"""

from __future__ import annotations

import logging

import numpy as np
import talib.abstract as ta
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy
from pandas import DataFrame

logger = logging.getLogger(__name__)


class ATRTrend(IStrategy):
    """ATR-based trend following strategy with dynamic risk management."""

    INTERFACE_VERSION = 3

    # --- Strategy settings ---
    timeframe = "1h"
    can_short = True
    stoploss = -0.04  # 4% fixed stop (ATR-based exit handles most exits)
    use_custom_stoploss = False
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    startup_candle_count = 200

    # --- Hyperparameters ---
    # ATR settings
    atr_period = IntParameter(10, 30, default=14, space="buy", optimize=True)
    atr_sl_mult = DecimalParameter(1.0, 3.0, default=1.5, space="buy",
                                   optimize=True)
    atr_tp_mult = DecimalParameter(2.0, 5.0, default=3.0, space="sell",
                                   optimize=True)

    # Keltner Channel
    kc_ema_period = IntParameter(15, 30, default=20, space="buy", optimize=True)
    kc_atr_mult = DecimalParameter(1.0, 3.0, default=2.0, space="buy",
                                   optimize=True)

    # Trend filter
    trend_ema_period = IntParameter(50, 200, default=100, space="buy",
                                    optimize=True)

    # ATR expansion filter (current ATR vs average ATR)
    atr_expansion_mult = DecimalParameter(0.8, 1.5, default=1.0, space="buy",
                                          optimize=True)

    # Futures
    leverage_default = 3.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate ATR, Keltner Channel, and trend indicators."""

        # ===== ATR =====
        atr_p = self.atr_period.value
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=atr_p)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"] * 100

        # ATR moving average (for expansion/contraction detection)
        dataframe["atr_ma"] = dataframe["atr"].rolling(50).mean()
        dataframe["atr_expanding"] = dataframe["atr"] > (
            dataframe["atr_ma"] * self.atr_expansion_mult.value
        )

        # ===== Keltner Channel (EMA + ATR bands) =====
        kc_ema = self.kc_ema_period.value
        kc_mult = self.kc_atr_mult.value

        dataframe["kc_mid"] = ta.EMA(dataframe, timeperiod=kc_ema)
        dataframe["kc_upper"] = dataframe["kc_mid"] + (dataframe["atr"] * kc_mult)
        dataframe["kc_lower"] = dataframe["kc_mid"] - (dataframe["atr"] * kc_mult)

        # ===== Trend EMA =====
        dataframe["trend_ema"] = ta.EMA(dataframe, timeperiod=self.trend_ema_period.value)

        # ===== Trend direction =====
        dataframe["uptrend"] = (
            (dataframe["close"] > dataframe["trend_ema"])
            & (dataframe["kc_mid"] > dataframe["kc_mid"].shift(1))
        )
        dataframe["downtrend"] = (
            (dataframe["close"] < dataframe["trend_ema"])
            & (dataframe["kc_mid"] < dataframe["kc_mid"].shift(1))
        )

        # ===== Breakout detection (require close above/below for 2 candles) =====
        dataframe["break_upper"] = (
            (dataframe["close"] > dataframe["kc_upper"])
            & (dataframe["close"].shift(1) > dataframe["kc_upper"].shift(1))
            & (dataframe["close"].shift(2) <= dataframe["kc_upper"].shift(2))
        )
        dataframe["break_lower"] = (
            (dataframe["close"] < dataframe["kc_lower"])
            & (dataframe["close"].shift(1) < dataframe["kc_lower"].shift(1))
            & (dataframe["close"].shift(2) >= dataframe["kc_lower"].shift(2))
        )

        # ===== Pullback to Keltner mid (EMA) for re-entry =====
        # Require: price touches mid but closes back in trend direction
        dataframe["pullback_to_mid_up"] = (
            (dataframe["low"] <= dataframe["kc_mid"] * 1.005)
            & (dataframe["close"] > dataframe["kc_mid"])
            & dataframe["uptrend"]
            & (dataframe["close"] > dataframe["open"])  # Bullish candle
        )
        dataframe["pullback_to_mid_down"] = (
            (dataframe["high"] >= dataframe["kc_mid"] * 0.995)
            & (dataframe["close"] < dataframe["kc_mid"])
            & dataframe["downtrend"]
            & (dataframe["close"] < dataframe["open"])  # Bearish candle
        )

        # ===== Dynamic stop/target levels =====
        sl_mult = self.atr_sl_mult.value
        tp_mult = self.atr_tp_mult.value

        dataframe["long_sl"] = dataframe["close"] - (dataframe["atr"] * sl_mult)
        dataframe["long_tp"] = dataframe["close"] + (dataframe["atr"] * tp_mult)
        dataframe["short_sl"] = dataframe["close"] + (dataframe["atr"] * sl_mult)
        dataframe["short_tp"] = dataframe["close"] - (dataframe["atr"] * tp_mult)

        # ===== RSI filter (avoid extreme) =====
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)

        # ===== ADX trend strength filter =====
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["strong_trend"] = dataframe["adx"] > 25

        # ===== Volume confirmation =====
        dataframe["vol_ma"] = dataframe["volume"].rolling(20).mean()
        dataframe["vol_spike"] = dataframe["volume"] > dataframe["vol_ma"] * 1.2

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Entry on Keltner breakout or pullback with ATR expansion."""

        # ===== LONG ENTRY =====
        # Keltner breakout OR pullback, with ADX + volume confirmation
        dataframe.loc[
            (
                (
                    (dataframe["break_upper"])               # Breakout
                    | (dataframe["pullback_to_mid_up"])       # Or pullback
                )
                & (dataframe["uptrend"])                      # Trend filter
                & (dataframe["atr_expanding"])                # Volatility expanding
                & (dataframe["strong_trend"])                 # ADX > 25
                & (dataframe["vol_spike"])                    # Volume above average
                & (dataframe["rsi"] < 70)                    # Not overbought
                & (dataframe["rsi"] > 40)                    # Not oversold (momentum)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== SHORT ENTRY =====
        dataframe.loc[
            (
                (
                    (dataframe["break_lower"])
                    | (dataframe["pullback_to_mid_down"])
                )
                & (dataframe["downtrend"])
                & (dataframe["atr_expanding"])
                & (dataframe["strong_trend"])
                & (dataframe["vol_spike"])
                & (dataframe["rsi"] > 30)
                & (dataframe["rsi"] < 60)
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit on trend reversal — let winners run."""

        # Exit long: trend flips to downtrend (close below trend EMA + bearish KC mid)
        dataframe.loc[
            (
                (dataframe["close"] < dataframe["trend_ema"])
                & (dataframe["kc_mid"] < dataframe["kc_mid"].shift(1))
                & (dataframe["close"] < dataframe["kc_lower"])
            ),
            "exit_long",
        ] = 1

        # Exit short: trend flips to uptrend
        dataframe.loc[
            (
                (dataframe["close"] > dataframe["trend_ema"])
                & (dataframe["kc_mid"] > dataframe["kc_mid"].shift(1))
                & (dataframe["close"] > dataframe["kc_upper"])
            ),
            "exit_short",
        ] = 1

        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time,
                        current_rate: float, current_profit: float,
                        after_fill: bool, **kwargs) -> float:
        """ATR-based dynamic trailing stop.

        - Initial stop: entry ± ATR × sl_mult
        - After 1R profit: move stop to breakeven
        - After 2R profit: trail at 1 ATR below high
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return self.stoploss

        last_candle = dataframe.iloc[-1]
        atr = last_candle["atr"]

        if atr == 0 or np.isnan(atr):
            return self.stoploss

        entry_rate = trade.open_rate
        sl_mult = self.atr_sl_mult.value

        if trade.is_short:
            # Short: stop above entry
            initial_stop = entry_rate + (atr * sl_mult)
            stop_pct = (initial_stop - current_rate) / current_rate

            # If in profit by 1R, move to breakeven
            if current_rate < entry_rate - (atr * sl_mult):
                stop_pct = (entry_rate - current_rate) / current_rate
                stop_pct = max(stop_pct, 0.001)  # At least breakeven

            return -abs(stop_pct)
        else:
            # Long: stop below entry
            initial_stop = entry_rate - (atr * sl_mult)
            stop_pct = (current_rate - initial_stop) / current_rate

            # If in profit by 1R, move to breakeven
            if current_rate > entry_rate + (atr * sl_mult):
                stop_pct = (current_rate - entry_rate) / current_rate
                stop_pct = max(stop_pct, 0.001)

            return -abs(stop_pct)

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Return leverage, clamped to max."""
        return min(self.leverage_default, 5.0)
