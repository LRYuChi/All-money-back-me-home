"""TAHZAN v5.7 — Context-Aware Momentum Hunter (Crypto Edition).

Ported from Taiwan stock market to OKX USDT perpetual futures.
A 4-kernel architecture:
  1. Macro Kernel: BTC-based market regime (GREEN/YELLOW/RED)
  2. Scoring Kernel: Multi-factor energy score (Adam + Chips + Squeeze + Resonance)
  3. Trigger Kernel: Killzone ORB breakout with volume & candle confirmation
  4. Survival Kernel: ATR trailing stop + time decay

Original design by user, crypto adaptation for Freqtrade.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy
from pandas import DataFrame
from smartmoneyconcepts import smc

_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from indicators.adam_projection import adam_projection
from indicators.squeeze_detector import squeeze_detector

logger = logging.getLogger(__name__)


class TAHZANCrypto(IStrategy):
    """TAHZAN v5.7 Crypto — Context-Aware Momentum Hunter."""

    INTERFACE_VERSION = 3

    # --- Strategy settings ---
    timeframe = "1h"
    can_short = True
    stoploss = -0.03  # 3% fixed stop
    use_custom_stoploss = False
    trailing_stop = False

    startup_candle_count = 200

    # --- Macro Kernel params ---
    macro_ema_fast = IntParameter(15, 30, default=20, space="buy", optimize=True)
    macro_ema_slow = IntParameter(40, 80, default=60, space="buy", optimize=True)

    # --- Scoring Kernel params ---
    adam_lookback = IntParameter(10, 40, default=20, space="buy", optimize=True)
    score_threshold_green = DecimalParameter(0.5, 0.8, default=0.68, space="buy", optimize=True)
    score_threshold_yellow = DecimalParameter(0.7, 0.95, default=0.80, space="buy", optimize=True)

    # --- Trigger Kernel params ---
    rvol_threshold = DecimalParameter(1.0, 2.5, default=1.5, space="buy", optimize=True)
    body_ratio_min = DecimalParameter(0.4, 0.8, default=0.6, space="buy", optimize=True)

    # --- Survival Kernel params ---
    atr_sl_mult = DecimalParameter(1.5, 3.5, default=2.5, space="buy", optimize=True)
    time_decay_hours = IntParameter(12, 48, default=24, space="sell", optimize=True)
    time_decay_min_profit = DecimalParameter(0.03, 0.08, default=0.05, space="sell", optimize=True)

    # --- Futures ---
    leverage_default = 3.0

    def informative_pairs(self):
        """Pull BTC data for macro regime + 4H for resonance."""
        pairs = self.dp.current_whitelist()
        result = [("BTC/USDT:USDT", "4h")]
        for pair in pairs:
            result.append((pair, "4h"))
        return result

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate all TAHZAN indicators across 4 kernels."""
        pair = metadata["pair"]

        # =======================================================
        # KERNEL 1: MACRO — BTC-based market regime
        # =======================================================
        btc_df = self.dp.get_pair_dataframe(pair="BTC/USDT:USDT", timeframe=self.timeframe)
        if len(btc_df) > 0:
            btc_df["btc_ema_fast"] = ta.EMA(btc_df, timeperiod=self.macro_ema_fast.value)
            btc_df["btc_ema_slow"] = ta.EMA(btc_df, timeperiod=self.macro_ema_slow.value)
            btc_df["btc_ema_fast_rising"] = btc_df["btc_ema_fast"] > btc_df["btc_ema_fast"].shift(3)

            # Regime: GREEN / YELLOW / RED
            # GREEN: BTC > EMA20 and EMA20 rising
            # YELLOW: BTC < EMA20 but > EMA60
            # RED: BTC < EMA60
            conditions = [
                (btc_df["close"] > btc_df["btc_ema_fast"]) & btc_df["btc_ema_fast_rising"],
                (btc_df["close"] < btc_df["btc_ema_fast"]) & (btc_df["close"] > btc_df["btc_ema_slow"]),
            ]
            choices = [2, 1]  # 2=GREEN, 1=YELLOW, 0=RED
            btc_df["macro_regime"] = np.select(conditions, choices, default=0)

            # Merge regime into current pair's dataframe
            btc_regime = btc_df[["date", "macro_regime"]].copy()
            btc_regime["date"] = pd.to_datetime(btc_regime["date"])
            dataframe["date"] = pd.to_datetime(dataframe["date"])
            dataframe = pd.merge_asof(
                dataframe.sort_values("date"),
                btc_regime.sort_values("date"),
                on="date",
                direction="backward",
            )
        else:
            dataframe["macro_regime"] = 2  # Default GREEN if no BTC data

        # Dynamic threshold based on regime
        dataframe["score_threshold"] = np.where(
            dataframe["macro_regime"] == 2,
            self.score_threshold_green.value,  # GREEN: lower bar
            np.where(
                dataframe["macro_regime"] == 1,
                self.score_threshold_yellow.value,  # YELLOW: higher bar
                999.0  # RED: impossible to pass
            )
        )

        # =======================================================
        # KERNEL 2: SCORING — Multi-factor energy score
        # =======================================================

        # --- f_Adam (40%): Adam Theory projection direction ---
        dataframe = adam_projection(dataframe, lookback=self.adam_lookback.value)
        dataframe["f_adam"] = np.where(dataframe["adam_slope"] > 0, 1.0, 0.0)

        # --- f_Chips (30%): Funding rate as institutional sentiment ---
        # Positive funding = longs pay shorts = crowd is long = bearish signal
        # Negative funding = shorts pay longs = crowd is short = bullish signal
        # We invert: negative funding → bullish for longs
        # For simplicity on 1H: use the direction of recent funding trend
        # Freqtrade stores funding_rate in the dataframe when available
        if "funding_rate" in dataframe.columns:
            fr = dataframe["funding_rate"].fillna(0)
            dataframe["f_chips_long"] = np.where(fr < 0, 1.0, np.where(fr < 0.0001, 0.5, 0.0))
            dataframe["f_chips_short"] = np.where(fr > 0.0001, 1.0, np.where(fr > 0, 0.5, 0.0))
        else:
            dataframe["f_chips_long"] = 0.5
            dataframe["f_chips_short"] = 0.5

        # --- f_Squeeze (20%): Volatility compression ---
        dataframe = squeeze_detector(dataframe)
        dataframe["f_squeeze"] = dataframe["squeeze_score"]

        # --- f_Resonance (10%): Multi-timeframe trend alignment ---
        # 1H trend direction
        swing_hl = smc.swing_highs_lows(dataframe, swing_length=10)
        bos_1h = smc.bos_choch(dataframe, swing_hl, close_break=True)
        dataframe["bos_1h"] = bos_1h["BOS"]

        # 4H trend direction
        htf_df = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        if len(htf_df) > 0:
            htf_swing = smc.swing_highs_lows(htf_df, swing_length=10)
            htf_bos = smc.bos_choch(htf_df, htf_swing, close_break=True)
            htf_df["bos_4h"] = htf_bos["BOS"]
            htf_merge = htf_df[["date", "bos_4h"]].copy()
            htf_merge["date"] = pd.to_datetime(htf_merge["date"])
            dataframe = pd.merge_asof(
                dataframe.sort_values("date"),
                htf_merge.sort_values("date"),
                on="date",
                direction="backward",
            )
        else:
            dataframe["bos_4h"] = np.nan

        # Compute running trend from BOS
        dataframe["trend_1h"] = _running_trend(dataframe["bos_1h"])
        dataframe["trend_4h"] = _running_trend(dataframe["bos_4h"])

        # Resonance: 1H and 4H agree
        dataframe["f_resonance_long"] = np.where(
            (dataframe["trend_1h"] > 0) & (dataframe["trend_4h"] > 0), 1.0, 0.0
        )
        dataframe["f_resonance_short"] = np.where(
            (dataframe["trend_1h"] < 0) & (dataframe["trend_4h"] < 0), 1.0, 0.0
        )

        # --- Total Score ---
        dataframe["score_long"] = (
            0.4 * dataframe["f_adam"]
            + 0.3 * dataframe["f_chips_long"]
            + 0.2 * dataframe["f_squeeze"]
            + 0.1 * dataframe["f_resonance_long"]
        )
        dataframe["score_short"] = (
            0.4 * (1.0 - dataframe["f_adam"])  # Inverted: bearish Adam = good for short
            + 0.3 * dataframe["f_chips_short"]
            + 0.2 * dataframe["f_squeeze"]
            + 0.1 * dataframe["f_resonance_short"]
        )

        # Score passes threshold?
        dataframe["score_pass_long"] = dataframe["score_long"] >= dataframe["score_threshold"]
        dataframe["score_pass_short"] = dataframe["score_short"] >= dataframe["score_threshold"]

        # =======================================================
        # KERNEL 3: TRIGGER — Killzone ORB breakout
        # =======================================================

        # Killzone time filter
        dataframe["utc_hour"] = dataframe["date"].dt.hour
        dataframe["in_killzone"] = (
            dataframe["utc_hour"].between(7, 10)
            | dataframe["utc_hour"].between(12, 14)
            | dataframe["utc_hour"].between(15, 17)
        )

        # ORB: high/low of first candle in each killzone session
        # Simplified: rolling 2-bar high/low as "session range"
        dataframe["orb_high"] = dataframe["high"].rolling(2).max()
        dataframe["orb_low"] = dataframe["low"].rolling(2).min()

        # Breakout (single bar confirmation for more signals)
        dataframe["break_up"] = (
            dataframe["close"] > dataframe["orb_high"].shift(1)
        )
        dataframe["break_down"] = (
            dataframe["close"] < dataframe["orb_low"].shift(1)
        )

        # RVol (Relative Volume)
        dataframe["vol_ma20"] = dataframe["volume"].rolling(20).mean()
        dataframe["rvol"] = dataframe["volume"] / (dataframe["vol_ma20"] + 1e-10)
        dataframe["rvol_pass"] = dataframe["rvol"] > self.rvol_threshold.value

        # Candle body ratio (solid body confirmation)
        candle_range = dataframe["high"] - dataframe["low"]
        candle_body = abs(dataframe["close"] - dataframe["open"])
        dataframe["body_ratio"] = candle_body / (candle_range + 1e-10)
        dataframe["body_pass"] = dataframe["body_ratio"] > self.body_ratio_min.value

        # =======================================================
        # KERNEL 4: SURVIVAL — ATR for stoploss
        # =======================================================
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """TAHZAN entry: Score passes + Trigger fires."""

        # ===== LONG =====
        # Macro: not RED
        # Score: passes dynamic threshold
        # Trigger: ORB breakout up + RVol + solid body + killzone
        dataframe.loc[
            (
                (dataframe["macro_regime"] >= 1)      # Not RED
                & (dataframe["score_pass_long"])       # Score passes
                & (dataframe["break_up"])              # ORB breakout up
                & (dataframe["rvol_pass"])             # Volume confirmation
                & (dataframe["body_pass"])             # Solid candle
                & (dataframe["in_killzone"])           # During killzone
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== SHORT =====
        # In RED regime, allow shorts even with lower threshold
        dataframe.loc[
            (
                (dataframe["score_pass_short"])        # Score passes
                & (dataframe["break_down"])            # ORB breakout down
                & (dataframe["rvol_pass"])             # Volume confirmation
                & (dataframe["body_pass"])             # Solid candle
                & (dataframe["in_killzone"])           # During killzone
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit on trend reversal (1H BOS against position)."""
        # Exit long: bearish BOS on 1H
        dataframe.loc[
            (dataframe["bos_1h"] == -1),
            "exit_long",
        ] = 1

        # Exit short: bullish BOS on 1H
        dataframe.loc[
            (dataframe["bos_1h"] == 1),
            "exit_short",
        ] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float,
                    **kwargs) -> str | bool:
        """Time Decay exit (Survival Kernel - Time Decay).

        If position held > time_decay_hours and profit < time_decay_min_profit,
        force exit — momentum has dissipated.
        """
        trade_duration = (current_time - trade.open_date_utc).total_seconds() / 3600

        if (trade_duration > self.time_decay_hours.value
                and current_profit < self.time_decay_min_profit.value):
            return "time_decay"

        return False

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Leverage adjusted by macro regime."""
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) > 0:
            regime = dataframe.iloc[-1].get("macro_regime", 2)
            if regime == 2:  # GREEN
                return min(self.leverage_default, 5.0)
            elif regime == 1:  # YELLOW
                return min(self.leverage_default * 0.5, 5.0)  # Half leverage
        return min(self.leverage_default, 5.0)


def _running_trend(bos_series: pd.Series) -> pd.Series:
    """Convert BOS signals to running trend direction."""
    trend = pd.Series(0, index=bos_series.index, dtype=int)
    current = 0
    for i in range(len(bos_series)):
        val = bos_series.iloc[i]
        if not pd.isna(val) and val != 0:
            current = int(val)
        trend.iloc[i] = current
    return trend
