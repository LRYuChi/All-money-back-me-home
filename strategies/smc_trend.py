"""SMC + Trend + ATR Strategy for crypto futures.

Combines Smart Money Concepts (ICT methodology) with trend following and ATR risk:
- 4H: BOS/CHoCH determines trend direction
- 1H: Order Block + FVG for entry zones
- ATR: Dynamic stop loss & take profit (volatility-adjusted)
- 1R Breakeven: When profit reaches 1× risk, move stop to entry price
- Killzone time filter for high-volume sessions
- Premium/Discount zone filter

Designed for USDT perpetual futures on OKX via Freqtrade.

References:
- smartmoneyconcepts package: https://github.com/joshyattridge/smart-money-concepts
- ICT methodology: Order Blocks, Fair Value Gaps, Break of Structure
"""

from __future__ import annotations

import logging
from datetime import datetime

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import talib.abstract as ta
from freqtrade.persistence import Trade
from freqtrade.strategy import DecimalParameter, IntParameter, IStrategy
from pandas import DataFrame
from smartmoneyconcepts import smc

# Add project root to path for indicators import
_proj_root = str(Path(__file__).resolve().parent.parent)
if _proj_root not in sys.path:
    sys.path.insert(0, _proj_root)

from indicators.adam_projection import adam_projection

try:
    from market_monitor.telegram_zh import (
        notify_entry, notify_exit, notify_stoploss, notify_pyramid,
        notify_startup, notify_confidence_change
    )
    _TG_AVAILABLE = True
except ImportError:
    _TG_AVAILABLE = False

logger = logging.getLogger(__name__)


class SMCTrend(IStrategy):
    """Smart Money Concepts + Trend Following strategy."""

    INTERFACE_VERSION = 3

    # --- Strategy settings ---
    timeframe = "1h"
    can_short = True
    stoploss = -0.03  # 3% initial stop
    use_custom_stoploss = False
    trailing_stop = False

    # Pyramid: allow adding to winning positions
    position_adjustment_enable = True
    max_entry_position_adjustment = 2  # Up to 2 add-ons (3 total entries)

    startup_candle_count = 200

    # --- Hyperparameters ---
    swing_length = IntParameter(5, 20, default=10, space="buy",
                                optimize=True)
    htf_swing_length = IntParameter(10, 30, default=15, space="buy",
                                    optimize=True)
    use_killzone = IntParameter(0, 1, default=1, space="buy",
                                optimize=True)
    ob_strength_min = DecimalParameter(0.1, 0.8, default=0.3, space="buy",
                                      optimize=True)

    # ATR-based risk management
    atr_period = IntParameter(10, 20, default=14, space="buy", optimize=True)
    atr_sl_mult = DecimalParameter(1.0, 3.0, default=1.5, space="buy",
                                   optimize=True)
    atr_tp_mult = DecimalParameter(2.0, 5.0, default=3.0, space="sell",
                                   optimize=True)

    # Adam Theory projection
    adam_lookback = IntParameter(10, 40, default=20, space="buy", optimize=True)
    use_adam_filter = IntParameter(0, 1, default=1, space="buy", optimize=True)

    # --- Confidence Engine settings ---
    max_leverage = DecimalParameter(1.0, 5.0, default=3.0, space="buy", optimize=True)

    # --- Futures settings ---
    leverage_default = 3.0

    def informative_pairs(self):
        """Pull 4H data for HTF trend bias."""
        pairs = self.dp.current_whitelist()
        return [(pair, "4h") for pair in pairs]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Calculate all SMC indicators."""
        pair = metadata["pair"]

        # =============================================
        # 1H (current timeframe) SMC indicators
        # =============================================
        sl = self.swing_length.value

        # Swing highs & lows
        swing_hl = smc.swing_highs_lows(dataframe, swing_length=sl)
        dataframe["swing_hl"] = swing_hl["HighLow"]
        dataframe["swing_level"] = swing_hl["Level"]

        # BOS / CHoCH (trend structure)
        bos_choch = smc.bos_choch(dataframe, swing_hl, close_break=True)
        dataframe["bos"] = bos_choch["BOS"]
        dataframe["choch"] = bos_choch["CHOCH"]
        dataframe["bos_level"] = bos_choch["Level"]

        # Order Blocks
        ob = smc.ob(dataframe, swing_hl, close_mitigation=True)
        dataframe["ob"] = ob["OB"]
        dataframe["ob_top"] = ob["Top"]
        dataframe["ob_bottom"] = ob["Bottom"]
        dataframe["ob_volume"] = ob.get("OBVolume", np.nan)
        dataframe["ob_pct"] = ob.get("Percentage", np.nan)
        dataframe["ob_mitigated"] = ob.get("MitigatedIndex", np.nan)

        # Fair Value Gaps
        fvg = smc.fvg(dataframe)
        dataframe["fvg"] = fvg["FVG"]
        dataframe["fvg_top"] = fvg["Top"]
        dataframe["fvg_bottom"] = fvg["Bottom"]
        dataframe["fvg_mitigated"] = fvg.get("MitigatedIndex", np.nan)

        # Liquidity levels
        liq = smc.liquidity(dataframe, swing_hl)
        dataframe["liquidity"] = liq["Liquidity"]
        dataframe["liq_level"] = liq["Level"]
        dataframe["liq_swept"] = liq.get("Swept", np.nan)

        # =============================================
        # 4H (HTF) trend bias via informative pairs
        # =============================================
        htf_df = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        if len(htf_df) > 0:
            htf_sl = self.htf_swing_length.value
            htf_swing = smc.swing_highs_lows(htf_df, swing_length=htf_sl)
            htf_bos = smc.bos_choch(htf_df, htf_swing, close_break=True)

            # Propagate HTF trend to 1H dataframe
            # Latest BOS direction = trend bias
            htf_df["htf_bos"] = htf_bos["BOS"]
            htf_df["htf_choch"] = htf_bos["CHOCH"]
            htf_df = htf_df[["date", "htf_bos", "htf_choch"]].copy()
            htf_df["date"] = pd.to_datetime(htf_df["date"])
            dataframe["date"] = pd.to_datetime(dataframe["date"])

            # Forward-fill HTF bias onto 1H candles
            dataframe = pd.merge_asof(
                dataframe.sort_values("date"),
                htf_df.sort_values("date"),
                on="date",
                direction="backward",
            )

            # Compute running trend from latest BOS
            dataframe["htf_trend"] = _compute_trend(dataframe, "htf_bos", "htf_choch")
        else:
            dataframe["htf_trend"] = 0
            dataframe["htf_bos"] = np.nan
            dataframe["htf_choch"] = np.nan

        # =============================================
        # Premium / Discount zones
        # =============================================
        dataframe = _add_premium_discount(dataframe)

        # =============================================
        # ATR for dynamic stop loss & take profit
        # =============================================
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=self.atr_period.value)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"] * 100

        # ATR-based stop and target levels (stored for custom_stoploss)
        sl_mult = self.atr_sl_mult.value
        tp_mult = self.atr_tp_mult.value
        dataframe["atr_sl_dist"] = dataframe["atr"] * sl_mult  # Distance in price
        dataframe["atr_tp_dist"] = dataframe["atr"] * tp_mult

        # =============================================
        # Adam Theory double reflection projection
        # =============================================
        dataframe = adam_projection(dataframe, lookback=self.adam_lookback.value)

        # =============================================
        # Killzone time filter (UTC)
        # =============================================
        dataframe["utc_hour"] = dataframe["date"].dt.hour
        dataframe["in_killzone"] = (
            dataframe["utc_hour"].between(7, 10)   # London Open
            | dataframe["utc_hour"].between(12, 14)  # New York Open
            | dataframe["utc_hour"].between(15, 17)  # London Close / Silver Bullet
        )

        # =============================================
        # Active OB/FVG zone detection
        # =============================================
        dataframe = _detect_active_zones(dataframe)

        # =============================================
        # NEW: Funding Rate filter (contrarian)
        # =============================================
        if "funding_rate" in dataframe.columns:
            fr = dataframe["funding_rate"].fillna(0)
            # Extreme positive funding → avoid longs (crowd overleveraged long)
            # Extreme negative funding → avoid shorts
            dataframe["fr_ok_long"] = fr < 0.0005   # < 0.05%/8h
            dataframe["fr_ok_short"] = fr > -0.0005  # > -0.05%/8h
        else:
            dataframe["fr_ok_long"] = True
            dataframe["fr_ok_short"] = True

        # =============================================
        # NEW: ATR volatility regime
        # =============================================
        atr_ma50 = dataframe["atr"].rolling(50).mean()
        dataframe["vol_regime_ok"] = (
            (dataframe["atr"] > atr_ma50 * 0.5)   # Not dead market
            & (dataframe["atr"] < atr_ma50 * 3.0)  # Not extreme chaos
        )

        # =============================================
        # NEW: OTE zone (61.8%-79% retracement)
        # =============================================
        dataframe["in_ote_long"] = (
            (dataframe["close"] >= dataframe["ote_bottom"])
            & (dataframe["close"] <= dataframe["ote_top"])
            & (dataframe["in_discount"] == True)
        )
        dataframe["in_ote_short"] = (
            (dataframe["close"] >= dataframe["range_high"] - (dataframe["range_high"] - dataframe["range_low"]) * 0.382)
            & (dataframe["in_premium"] == True)
        )

        # =============================================
        # NEW: Recent liquidity sweep detection
        # =============================================
        # Check if liquidity was swept in the last 5 candles
        dataframe["recent_liq_sweep"] = False
        if "liq_swept" in dataframe.columns:
            for lookback in range(1, 6):
                swept = dataframe["liq_swept"].shift(lookback)
                dataframe["recent_liq_sweep"] = dataframe["recent_liq_sweep"] | (swept == 1)

        # =============================================
        # CONFIDENCE ENGINE (backtest-compatible)
        # =============================================
        # Builds a 0.0-1.0 confidence score from data available in the dataframe.
        # In live mode, the full GlobalConfidenceEngine supplements this.
        dataframe = _calculate_confidence(dataframe)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Define entry conditions using SMC confluence."""

        killzone_filter = (
            (dataframe["in_killzone"]) | (self.use_killzone.value == 0)
        )

        # Adam Theory directional confluence filter
        adam_long_filter = (
            (dataframe["adam_bullish"] == True) | (self.use_adam_filter.value == 0)
        )
        adam_short_filter = (
            (dataframe["adam_bullish"] == False) | (self.use_adam_filter.value == 0)
        )

        # Confidence gate: block all trades in HIBERNATE mode
        confidence_ok = dataframe["confidence"] > 0.2

        # ===== LONG ENTRY =====
        dataframe.loc[
            (
                (dataframe["htf_trend"] > 0)                    # 4H bullish
                & (dataframe["in_ote_long"] == True)             # OTE zone (discount)
                & (
                    (dataframe["in_bullish_ob"] == True)          # At bullish OB
                    | (dataframe["in_bullish_fvg"] == True)       # Or at bullish FVG
                )
                & adam_long_filter                                # Adam projection up
                & (dataframe["fr_ok_long"] == True)              # Funding rate OK
                & (dataframe["vol_regime_ok"] == True)           # Volatility normal
                & confidence_ok                                   # Confidence > HIBERNATE
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1

        # ===== SHORT ENTRY =====
        dataframe.loc[
            (
                (dataframe["htf_trend"] < 0)                    # 4H bearish
                & (dataframe["in_ote_short"] == True)            # Premium zone OTE
                & (
                    (dataframe["in_bearish_ob"] == True)          # At bearish OB
                    | (dataframe["in_bearish_fvg"] == True)       # Or at bearish FVG
                )
                & adam_short_filter                               # Adam projection down
                & (dataframe["fr_ok_short"] == True)             # Funding rate OK
                & (dataframe["vol_regime_ok"] == True)           # Volatility normal
                & confidence_ok                                   # Confidence > HIBERNATE
                & killzone_filter
                & (dataframe["volume"] > 0)
            ),
            "enter_short",
        ] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Exit on structure break against position."""
        # Exit long: bearish CHoCH on 1H (trend reversal signal)
        dataframe.loc[
            (dataframe["choch"] == -1),
            "exit_long",
        ] = 1

        # Exit short: bullish CHoCH on 1H
        dataframe.loc[
            (dataframe["choch"] == 1),
            "exit_short",
        ] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time: datetime,
                            entry_tag: str | None, side: str, **kwargs) -> bool:
        """進場確認 — 發送繁體中文 Telegram 通知."""
        if _TG_AVAILABLE:
            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            confidence = 0.5
            if len(dataframe) > 0:
                confidence = dataframe.iloc[-1].get("confidence", 0.5)
            lev = 1.0 + (self.max_leverage.value - 1.0) * (confidence ** 2)
            notify_entry(
                pair=pair, side=side, rate=rate,
                stake=amount * rate, leverage=round(lev, 1),
                confidence=confidence,
                reason=f"SMC OB/FVG + 亞當投影 + OTE"
            )
        return True

    def confirm_trade_exit(self, pair: str, trade: Trade, order_type: str,
                           amount: float, rate: float, time_in_force: str,
                           exit_reason: str, current_time: datetime,
                           **kwargs) -> bool:
        """出場確認 — 發送繁體中文 Telegram 通知."""
        if _TG_AVAILABLE:
            profit_pct = trade.calc_profit_ratio(rate) * 100
            profit_usdt = trade.calc_profit(rate)
            duration = str(current_time - trade.open_date_utc).split(".")[0]
            side = "short" if trade.is_short else "long"

            dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
            confidence = 0.5
            if len(dataframe) > 0:
                confidence = dataframe.iloc[-1].get("confidence", 0.5)

            reason_zh = {
                "exit_signal": "📊 結構反轉 (CHoCH)",
                "stop_loss": "🛑 觸發止損",
                "trailing_stop_loss": "📈 追蹤止損",
                "force_exit": "⚡ 強制出場",
            }.get(exit_reason, exit_reason)

            if "stop_loss" in exit_reason:
                notify_stoploss(pair, side, profit_pct, profit_usdt)
            else:
                notify_exit(
                    pair=pair, side=side, profit_pct=profit_pct,
                    profit_usdt=profit_usdt, exit_reason=reason_zh,
                    duration=duration, confidence=confidence
                )
        return True

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        """Continuous leverage scaling by confidence.

        leverage = 1.0 + (max_leverage - 1.0) × confidence²
        Quadratic curve: confidence must be HIGH to get full leverage.
          conf=0.3 → 1.18x
          conf=0.5 → 1.5x
          conf=0.7 → 1.98x
          conf=0.9 → 2.62x
          conf=1.0 → 3.0x (max)
        """
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 1.0

        confidence = dataframe.iloc[-1].get("confidence", 0.5)
        max_lev = self.max_leverage.value

        # Quadratic scaling: aggressive only at high confidence
        lev = 1.0 + (max_lev - 1.0) * (confidence ** 2)
        return min(max(lev, 1.0), max_leverage)

    def custom_stake_amount(self, current_time, current_rate: float,
                            proposed_stake: float, min_stake: float | None,
                            max_stake: float, leverage: float,
                            entry_tag: str | None, side: str,
                            **kwargs) -> float:
        """Continuous position sizing by confidence.

        scale = 0.3 + 0.9 × confidence  (range: 0.3x to 1.2x)
          conf=0.2 → 0.48x (defensive)
          conf=0.5 → 0.75x (cautious)
          conf=0.7 → 0.93x (normal)
          conf=0.9 → 1.11x (aggressive)
          conf=1.0 → 1.20x (max wind)
        """
        pair = kwargs.get("pair", "")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return proposed_stake

        confidence = dataframe.iloc[-1].get("confidence", 0.5)

        # Linear scaling with floor and ceiling
        scale = 0.3 + 0.9 * confidence
        adjusted = proposed_stake * scale

        if min_stake is not None:
            adjusted = max(adjusted, min_stake)
        return min(adjusted, max_stake)

    def adjust_trade_position(self, trade: Trade, current_time: datetime,
                              current_rate: float, current_profit: float,
                              min_stake: float | None, max_stake: float,
                              current_entry_rate: float, current_exit_rate: float,
                              current_entry_profit: float, current_exit_profit: float,
                              **kwargs) -> float | None:
        """Pyramid into winning positions when confidence is high.

        Rules:
        1. Profit >= 5% (clear separation from entry)
        2. Confidence >= 0.7 (NORMAL or AGGRESSIVE environment)
        3. HTF trend still aligned with position direction
        4. Each add-on is 50% of original position
        5. Max 2 add-ons (3 total entries per trade)
        """
        # Only add, never reduce via this method
        if current_profit < 0.05:
            return None  # Not enough profit separation

        # Check how many times we've already added
        filled_entries = trade.nr_of_successful_entries
        if filled_entries >= 3:
            return None  # Already at max pyramid

        pair = trade.pair
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None

        last = dataframe.iloc[-1]
        confidence = last.get("confidence", 0.5)
        htf_trend = last.get("htf_trend", 0)

        # Must have high confidence
        if confidence < 0.7:
            return None

        # Trend must still agree with position direction
        if trade.is_short and htf_trend > 0:
            return None  # Short but trend turned bullish
        if not trade.is_short and htf_trend < 0:
            return None  # Long but trend turned bearish

        # Progressive add-on sizing based on confidence
        # 1st add-on: 50% of original
        # 2nd add-on: 30% of original
        if filled_entries == 1:
            addon_ratio = 0.5
        elif filled_entries == 2:
            addon_ratio = 0.3
        else:
            return None

        # Scale by confidence: higher confidence = larger add-on
        addon_ratio *= confidence

        try:
            stake = trade.stake_amount * addon_ratio
        except Exception:
            return None

        if min_stake is not None and stake < min_stake:
            return None
        if stake > max_stake:
            stake = max_stake

        logger.info(
            "Pyramid add-on #%d for %s: +%.2f USDT (profit=%.1f%%, confidence=%.2f)",
            filled_entries, pair, stake, current_profit * 100, confidence
        )

        if _TG_AVAILABLE:
            notify_pyramid(pair, filled_entries, stake, current_profit * 100, confidence)

        return stake


# =============================================
# Helper functions
# =============================================

def _compute_trend(df: DataFrame, bos_col: str, choch_col: str) -> pd.Series:
    """Compute running trend direction from BOS/CHoCH signals.

    Returns: Series of 1 (bullish), -1 (bearish), 0 (neutral)
    """
    trend = pd.Series(0, index=df.index, dtype=int)
    current = 0
    for i in range(len(df)):
        bos_val = df[bos_col].iloc[i]
        choch_val = df[choch_col].iloc[i]

        if not pd.isna(bos_val):
            current = int(bos_val)  # 1 = bullish BOS, -1 = bearish BOS
        elif not pd.isna(choch_val):
            current = int(choch_val)  # CHoCH reverses trend

        trend.iloc[i] = current
    return trend


def _add_premium_discount(df: DataFrame) -> DataFrame:
    """Add premium/discount zone based on recent swing range."""
    # Find recent swing high and low (rolling window)
    window = 50
    df["range_high"] = df["high"].rolling(window).max()
    df["range_low"] = df["low"].rolling(window).min()
    df["equilibrium"] = (df["range_high"] + df["range_low"]) / 2

    # Premium = above equilibrium, Discount = below
    df["in_premium"] = df["close"] > df["equilibrium"]
    df["in_discount"] = df["close"] < df["equilibrium"]

    # OTE zone (61.8% - 79% retracement)
    range_size = df["range_high"] - df["range_low"]
    df["ote_top"] = df["range_high"] - (range_size * 0.618)
    df["ote_bottom"] = df["range_high"] - (range_size * 0.79)

    return df


def _detect_active_zones(df: DataFrame) -> DataFrame:
    """Detect if current price is within an active (unmitigated) OB or FVG."""

    df["in_bullish_ob"] = False
    df["in_bearish_ob"] = False
    df["in_bullish_fvg"] = False
    df["in_bearish_fvg"] = False

    # Track active (unmitigated) order blocks
    active_obs = []  # list of (type, top, bottom, index)

    for i in range(len(df)):
        ob_val = df["ob"].iloc[i]
        close = df["close"].iloc[i]

        # Register new order blocks
        if not pd.isna(ob_val) and ob_val != 0:
            top = df["ob_top"].iloc[i]
            bottom = df["ob_bottom"].iloc[i]
            if not pd.isna(top) and not pd.isna(bottom):
                active_obs.append({
                    "type": int(ob_val),  # 1=bullish, -1=bearish
                    "top": top,
                    "bottom": bottom,
                    "created": i,
                })

        # Check if price is in any active OB
        remaining = []
        for ob_zone in active_obs:
            # Check mitigation (price closed through the OB)
            if ob_zone["type"] == 1 and close < ob_zone["bottom"]:
                continue  # Mitigated bullish OB
            if ob_zone["type"] == -1 and close > ob_zone["top"]:
                continue  # Mitigated bearish OB
            # Remove old OBs (older than 72 candles = 3 days)
            if i - ob_zone["created"] > 72:
                continue

            remaining.append(ob_zone)

            # Check if price is within OB zone
            if ob_zone["bottom"] <= close <= ob_zone["top"]:
                if ob_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_ob"] = True
                else:
                    df.at[df.index[i], "in_bearish_ob"] = True

        active_obs = remaining

    # Track active FVGs (simpler — just check if current price fills gap)
    active_fvgs = []

    for i in range(len(df)):
        fvg_val = df["fvg"].iloc[i]
        close = df["close"].iloc[i]

        if not pd.isna(fvg_val) and fvg_val != 0:
            top = df["fvg_top"].iloc[i]
            bottom = df["fvg_bottom"].iloc[i]
            if not pd.isna(top) and not pd.isna(bottom):
                active_fvgs.append({
                    "type": int(fvg_val),
                    "top": top,
                    "bottom": bottom,
                    "created": i,
                })

        remaining = []
        for fvg_zone in active_fvgs:
            # Remove old FVGs (48h = 2 days for crypto speed)
            if i - fvg_zone["created"] > 48:
                continue

            # Mitigated: price closed through the FVG (filled the gap)
            if fvg_zone["type"] == 1 and close < fvg_zone["bottom"]:
                continue  # Bullish FVG broken below → mitigated
            if fvg_zone["type"] == -1 and close > fvg_zone["top"]:
                continue  # Bearish FVG broken above → mitigated

            remaining.append(fvg_zone)

            # Check if price is within active FVG zone
            if fvg_zone["bottom"] <= close <= fvg_zone["top"]:
                if fvg_zone["type"] == 1:
                    df.at[df.index[i], "in_bullish_fvg"] = True
                else:
                    df.at[df.index[i], "in_bearish_fvg"] = True

        active_fvgs = remaining

    return df


def _calculate_confidence(df: DataFrame) -> DataFrame:
    """Calculate confidence score — optimized to capture favorable environments.

    Five factors designed to detect and ride momentum windows:

    1. Momentum (30%): Multi-period price momentum — catches the wind
    2. Trend alignment (25%): HTF trend direction + strength — confirms direction
    3. Volume conviction (15%): Volume expanding with trend — smart money participating
    4. Volatility quality (15%): ATR expanding in trend = good; ATR spiking in chop = bad
    5. Market health (15%): Price vs MA structure — overall regime

    Key design: FAST response to regime changes, AGGRESSIVE when aligned.
    """
    n = len(df)
    close_s = pd.Series(df["close"].values, index=df.index)
    high_s = pd.Series(df["high"].values, index=df.index)
    low_s = pd.Series(df["low"].values, index=df.index)
    atr_s = pd.Series(df.get("atr", pd.Series(np.zeros(n))).values, index=df.index)
    volume_s = pd.Series(df["volume"].values, index=df.index)
    htf_trend = df.get("htf_trend", pd.Series(np.zeros(n), index=df.index)).values

    # ==========================================================
    # 1. MOMENTUM SANDBOX (30%) — "Is the wind blowing?"
    # ==========================================================
    # Multi-period ROC: 6h, 24h, 72h momentum
    roc_6 = close_s.pct_change(6)    # 6-hour momentum
    roc_24 = close_s.pct_change(24)  # 1-day momentum
    roc_72 = close_s.pct_change(72)  # 3-day momentum

    # Positive momentum across all timeframes = strong wind
    # Convert each to 0-1 score: +5% = 0.8, -5% = 0.2
    mom_6 = np.clip(0.5 + roc_6.fillna(0) * 8, 0.05, 0.95)
    mom_24 = np.clip(0.5 + roc_24.fillna(0) * 5, 0.05, 0.95)
    mom_72 = np.clip(0.5 + roc_72.fillna(0) * 3, 0.05, 0.95)

    # Multi-period alignment bonus: all three agree = extra boost
    all_positive = (roc_6 > 0) & (roc_24 > 0) & (roc_72 > 0)
    all_negative = (roc_6 < 0) & (roc_24 < 0) & (roc_72 < 0)
    alignment_bonus = np.where(all_positive | all_negative, 0.15, 0.0)

    momentum_score = (mom_6 * 0.4 + mom_24 * 0.35 + mom_72 * 0.25 + alignment_bonus)
    momentum_score = np.clip(momentum_score, 0, 1)

    # ==========================================================
    # 2. TREND ALIGNMENT (25%) — "Is the structure confirmed?"
    # ==========================================================
    # HTF trend direction (instant, not streak-based)
    # Trend present = 0.7 base; no trend = 0.3
    trend_present = np.where(htf_trend != 0, 0.7, 0.3)

    # Trend agrees with short-term momentum = extra confidence
    trend_momentum_agree = np.where(
        ((htf_trend > 0) & (roc_24.fillna(0) > 0)) |
        ((htf_trend < 0) & (roc_24.fillna(0) < 0)),
        0.3, 0.0
    )

    trend_score = np.clip(trend_present + trend_momentum_agree, 0, 1)

    # ==========================================================
    # 3. VOLUME CONVICTION (15%) — "Is smart money participating?"
    # ==========================================================
    vol_ma20 = volume_s.rolling(20, min_periods=5).mean()
    vol_ratio = (volume_s / (vol_ma20 + 1e-10)).fillna(1)

    # Volume expanding = conviction; volume dying = distribution
    # ratio > 1.5 = strong, 1.0 = normal, < 0.5 = dead
    vol_score = np.clip(vol_ratio * 0.5, 0.1, 0.95)

    # Volume expanding WITH trend = extra good
    vol_trend_agree = np.where(
        (vol_ratio > 1.2) & (htf_trend != 0),
        0.15, 0.0
    )
    volume_score = np.clip(vol_score + vol_trend_agree, 0, 1)

    # ==========================================================
    # 4. VOLATILITY QUALITY (15%) — "Is it the right kind of vol?"
    # ==========================================================
    if n > 50:
        atr_ma50 = atr_s.rolling(50, min_periods=10).mean()
        atr_ratio = (atr_s / (atr_ma50 + 1e-10)).fillna(1)

        # KEY INSIGHT: ATR expanding in a trend = GOOD (momentum)
        #              ATR expanding without trend = BAD (chaos)
        #              ATR contracting = preparing (neutral-to-good)
        trending = htf_trend != 0
        vol_expanding = atr_ratio > 1.2
        vol_contracting = atr_ratio < 0.7

        vol_quality = np.where(
            trending & vol_expanding, 0.85,        # Trend + expanding = excellent
            np.where(
                vol_contracting, 0.65,             # Contracting = squeeze building
                np.where(
                    ~trending & vol_expanding, 0.25, # No trend + expanding = chaos
                    0.55                             # Normal
                )
            )
        )
    else:
        vol_quality = np.full(n, 0.5)

    volatility_score = np.clip(vol_quality, 0, 1)

    # ==========================================================
    # 5. MARKET HEALTH (15%) — "Is the environment supportive?"
    # ==========================================================
    # Price vs EMA50 and EMA200
    ema50 = close_s.ewm(span=50, min_periods=20).mean()
    ema200 = close_s.ewm(span=200, min_periods=50).mean()

    # Bullish structure: price > EMA50 > EMA200
    bull_structure = (close_s > ema50) & (ema50 > ema200)
    bear_structure = (close_s < ema50) & (ema50 < ema200)
    price_above_ema50 = close_s > ema50

    health_score = np.where(
        bull_structure, 0.85,
        np.where(
            bear_structure, 0.15,  # Clear downtrend
            np.where(
                price_above_ema50, 0.65,  # Mixed but above 50
                0.35  # Mixed but below 50
            )
        )
    )

    # Funding rate overlay (if available)
    if "funding_rate" in df.columns:
        fr = df["funding_rate"].fillna(0).values
        # Extreme funding warns of overleverage
        fr_penalty = np.where(np.abs(fr) > 0.0003, -0.15, 0.0)  # > 0.03%/8h
        health_score = np.clip(health_score + fr_penalty, 0, 1)

    # ==========================================================
    # COMBINE — weighted sum
    # ==========================================================
    raw_confidence = (
        0.30 * momentum_score
        + 0.25 * trend_score
        + 0.15 * volume_score
        + 0.15 * volatility_score
        + 0.15 * health_score
    )

    # FAST EMA smoothing (span=5 = 5 hours, responsive to changes)
    conf_series = pd.Series(np.asarray(raw_confidence).flatten()).ewm(span=5, min_periods=1).mean()
    df["confidence"] = np.clip(conf_series.values, 0.0, 1.0)

    # Regime labels
    df["conf_regime"] = pd.cut(
        df["confidence"],
        bins=[0, 0.2, 0.4, 0.6, 0.8, 1.01],
        labels=["HIBERNATE", "DEFENSIVE", "CAUTIOUS", "NORMAL", "AGGRESSIVE"],
    )

    return df
