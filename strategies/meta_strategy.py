"""MetaStrategy — Adaptive Strategy Selection System.

Detects market regime from OHLCV data across multiple timeframes,
then selects the best sub-engine for that regime.

Architecture:
  4H/1D indicators → Regime Detection → Engine Selection
  ├── TRENDING_BULL  → TrendEngine (Supertrend 4-layer, long only, 3-5x lev)
  ├── TRENDING_BEAR  → TrendEngine (Supertrend 4-layer, short only, 3-5x lev)
  ├── ACCUMULATION   → SqueezeEngine (BB/KC squeeze breakout, 2x lev)
  ├── HIGH_VOLATILITY → SafeEngine (no trading)
  └── RANGING         → SafeEngine (no trading)

Sub-engines share 15m as base timeframe. Each engine has independent
entry/exit logic selected by the regime tag.

Designed for USDT perpetual futures on OKX via Freqtrade.
"""

from __future__ import annotations

import logging
import numpy as np
import pandas as pd
import talib.abstract as ta
from datetime import datetime
from freqtrade.persistence import Trade
from freqtrade.strategy import IStrategy, stoploss_from_open
from pandas import DataFrame

logger = logging.getLogger(__name__)


# ============================================================
# SUPERTREND CALCULATION (shared utility)
# ============================================================

def _calc_supertrend(df: DataFrame, period: int = 10, multiplier: float = 3.0) -> DataFrame:
    atr = ta.ATR(df, timeperiod=period)
    src = (df["high"].values + df["low"].values) / 2
    close = df["close"].values
    atr_vals = atr.values
    n = len(df)
    up, dn, trend = np.zeros(n), np.zeros(n), np.ones(n)

    for i in range(n):
        if np.isnan(atr_vals[i]):
            up[i] = dn[i] = src[i]; continue
        raw_up = src[i] - multiplier * atr_vals[i]
        raw_dn = src[i] + multiplier * atr_vals[i]
        if i > 0:
            up[i] = max(raw_up, up[i-1]) if close[i-1] > up[i-1] else raw_up
            dn[i] = min(raw_dn, dn[i-1]) if close[i-1] < dn[i-1] else raw_dn
        else:
            up[i], dn[i] = raw_up, raw_dn
        if i > 0:
            if trend[i-1] == -1 and close[i] > dn[i-1]: trend[i] = 1
            elif trend[i-1] == 1 and close[i] < up[i-1]: trend[i] = -1
            else: trend[i] = trend[i-1]

    df["st_up"], df["st_dn"], df["st_trend"] = up, dn, trend
    return df


# ============================================================
# REGIME DETECTION (pure OHLCV, backtestable)
# ============================================================

def _detect_regime(df_4h: DataFrame) -> pd.Series:
    """Classify market regime from 4H OHLCV data.

    Returns Series of regime labels aligned to df_4h index.
    """
    close = df_4h["close"]
    ema50 = ta.EMA(df_4h, timeperiod=50)
    ema200 = ta.EMA(df_4h, timeperiod=200)
    adx = ta.ADX(df_4h, timeperiod=14)
    atr = ta.ATR(df_4h, timeperiod=14)
    atr_ma = atr.rolling(50, min_periods=10).mean()
    atr_ratio = (atr / atr_ma.replace(0, np.nan)).fillna(1.0)

    n = len(df_4h)
    regime = pd.Series("RANGING", index=df_4h.index)

    # Priority order: HIGH_VOL > TRENDING > ACCUMULATION > RANGING
    regime[atr_ratio > 2.0] = "HIGH_VOLATILITY"

    bull = (adx > 25) & (close > ema50) & (ema50 > ema200) & (atr_ratio <= 2.0)
    regime[bull] = "TRENDING_BULL"

    bear = (adx > 25) & (close < ema50) & (ema50 < ema200) & (atr_ratio <= 2.0)
    regime[bear] = "TRENDING_BEAR"

    accum = (adx < 20) & (atr_ratio < 0.8) & ~bull & ~bear & (atr_ratio <= 2.0)
    regime[accum] = "ACCUMULATION"

    return regime


# ============================================================
# SUB-ENGINE: TREND (Supertrend 4-layer MTF)
# ============================================================

def _trend_engine(df: DataFrame) -> tuple[pd.Series, pd.Series]:
    """Supertrend multi-TF trend following signals.

    Long: 15m flips bull + 1H bull + 1D bull + ADX/Vol/ATR quality
    Short: 15m flips bear + 1H bear + 1D bear + quality
    """
    st_buy = (df["st_trend"] == 1) & (df["st_trend"].shift(1) == -1)
    st_sell = (df["st_trend"] == -1) & (df["st_trend"].shift(1) == 1)

    quality = (
        (df["adx"] > 25)
        & (df["volume"] > df["volume_ma_20"] * 1.2)
        & df["atr_rising"]
    )

    all_bull = (df["st_1h"] == 1) & (df["st_1d"] == 1)
    all_bear = (df["st_1h"] == -1) & (df["st_1d"] == -1)

    enter_long = st_buy & all_bull & quality
    enter_short = st_sell & all_bear & quality

    return enter_long, enter_short


# ============================================================
# SUB-ENGINE: SQUEEZE (BB/KC breakout)
# ============================================================

def _squeeze_engine(df: DataFrame) -> tuple[pd.Series, pd.Series]:
    """BB/KC squeeze breakout signals.

    Enter when squeeze releases (BB expands outside KC) with momentum direction.
    """
    squeeze_fire = df.get("squeeze_fire", pd.Series(False, index=df.index))
    momentum = df.get("momentum", pd.Series(0, index=df.index))
    mom_rising = df.get("mom_rising", pd.Series(False, index=df.index))
    vol_ok = df["volume"] > df["volume_ma_20"]

    enter_long = squeeze_fire & (momentum > 0) & mom_rising & vol_ok
    enter_short = squeeze_fire & (momentum < 0) & ~mom_rising & vol_ok

    return enter_long, enter_short


# ============================================================
# META STRATEGY
# ============================================================

class MetaStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "15m"
    startup_candle_count = 250

    stoploss = -0.05
    trailing_stop = False
    use_custom_stoploss = False

    can_short = True
    trading_mode = "futures"
    margin_mode = "isolated"

    def informative_pairs(self):
        pairs = self.dp.current_whitelist()
        return ([(p, "1h") for p in pairs]
                + [(p, "4h") for p in pairs]
                + [(p, "1d") for p in pairs])

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        pair = metadata["pair"]

        # === 15m base indicators ===
        dataframe = _calc_supertrend(dataframe, 10, 3.0)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=10)
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["volume_ma_20"] = ta.SMA(dataframe["volume"], timeperiod=20)
        dataframe["atr_rising"] = dataframe["atr"] > dataframe["atr"].shift(4)

        # BB/KC Squeeze indicators (for SqueezeEngine)
        bb = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe["bb_upper"] = bb["upperband"]
        dataframe["bb_lower"] = bb["lowerband"]
        kc_mid = ta.EMA(dataframe, timeperiod=20)
        kc_atr = ta.ATR(dataframe, timeperiod=14)
        dataframe["kc_upper"] = kc_mid + 2.0 * kc_atr
        dataframe["kc_lower"] = kc_mid - 2.0 * kc_atr

        dataframe["squeeze_on"] = (dataframe["bb_lower"] > dataframe["kc_lower"]) & (dataframe["bb_upper"] < dataframe["kc_upper"])
        dataframe["squeeze_fire"] = ~dataframe["squeeze_on"] & dataframe["squeeze_on"].shift(1).fillna(False)

        highest = dataframe["high"].rolling(12).max()
        lowest = dataframe["low"].rolling(12).min()
        dataframe["momentum"] = dataframe["close"] - (highest + lowest + kc_mid) / 3
        dataframe["mom_rising"] = dataframe["momentum"] > dataframe["momentum"].shift(1)

        # === 1H Supertrend ===
        htf1h = self.dp.get_pair_dataframe(pair=pair, timeframe="1h")
        if len(htf1h) > 0:
            htf1h = _calc_supertrend(htf1h, 10, 3.0)
            m = htf1h[["date", "st_trend"]].rename(columns={"st_trend": "st_1h"}).copy()
            m["date"] = pd.to_datetime(m["date"])
            dataframe["date"] = pd.to_datetime(dataframe["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["st_1h"] = 0

        # === 4H Regime Detection ===
        htf4h = self.dp.get_pair_dataframe(pair=pair, timeframe="4h")
        if len(htf4h) > 0:
            htf4h["regime"] = _detect_regime(htf4h)
            m4 = htf4h[["date", "regime"]].copy()
            m4["date"] = pd.to_datetime(m4["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m4.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["regime"] = "RANGING"

        # === 1D Supertrend ===
        htf1d = self.dp.get_pair_dataframe(pair=pair, timeframe="1d")
        if len(htf1d) > 0:
            htf1d = _calc_supertrend(htf1d, 10, 3.0)
            m1d = htf1d[["date", "st_trend"]].rename(columns={"st_trend": "st_1d"}).copy()
            m1d["date"] = pd.to_datetime(m1d["date"])
            dataframe = pd.merge_asof(dataframe.sort_values("date"), m1d.sort_values("date"),
                                       on="date", direction="backward")
        else:
            dataframe["st_1d"] = 0

        # Log regime distribution
        if len(dataframe) > 0:
            regime_counts = dataframe["regime"].value_counts()
            logger.info("MetaStrategy %s regimes: %s", pair,
                        " | ".join(f"{r}={c}" for r, c in regime_counts.items()))

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Get signals from each engine
        trend_long, trend_short = _trend_engine(dataframe)
        squeeze_long, squeeze_short = _squeeze_engine(dataframe)

        # Regime masks
        is_bull = dataframe["regime"] == "TRENDING_BULL"
        is_bear = dataframe["regime"] == "TRENDING_BEAR"
        is_accum = dataframe["regime"] == "ACCUMULATION"

        # Route signals by regime
        # TRENDING_BULL → TrendEngine long only
        mask_trend_long = is_bull & trend_long
        dataframe.loc[mask_trend_long, "enter_long"] = 1
        dataframe.loc[mask_trend_long, "enter_tag"] = "trend_long"

        # TRENDING_BEAR → TrendEngine short only
        mask_trend_short = is_bear & trend_short
        dataframe.loc[mask_trend_short, "enter_short"] = 1
        dataframe.loc[mask_trend_short, "enter_tag"] = "trend_short"

        # ACCUMULATION → SqueezeEngine both directions
        mask_sq_long = is_accum & squeeze_long & ~mask_trend_long
        dataframe.loc[mask_sq_long, "enter_long"] = 1
        dataframe.loc[mask_sq_long, "enter_tag"] = "squeeze_long"

        mask_sq_short = is_accum & squeeze_short & ~mask_trend_short
        dataframe.loc[mask_sq_short, "enter_short"] = 1
        dataframe.loc[mask_sq_short, "enter_tag"] = "squeeze_short"

        # HIGH_VOLATILITY / RANGING → no signals

        # Log
        n_long = int(dataframe.get("enter_long", 0).sum())
        n_short = int(dataframe.get("enter_short", 0).sum())
        logger.info("MetaStrategy %s: %d long (%d trend, %d squeeze), %d short (%d trend, %d squeeze)",
                    metadata.get("pair", "?"), n_long,
                    int(mask_trend_long.sum()), int(mask_sq_long.sum()),
                    n_short, int(mask_trend_short.sum()), int(mask_sq_short.sum()))

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Trend exits: 15m Supertrend flip (when 1H also flips)
        st_sell = (dataframe["st_trend"] == -1) & (dataframe["st_trend"].shift(1) == 1)
        st_buy = (dataframe["st_trend"] == 1) & (dataframe["st_trend"].shift(1) == -1)

        dataframe.loc[st_sell & (dataframe["st_1h"] != 1), "exit_long"] = 1
        dataframe.loc[st_buy & (dataframe["st_1h"] != -1), "exit_short"] = 1

        return dataframe

    def custom_exit(self, pair: str, trade: Trade, current_time: datetime,
                    current_rate: float, current_profit: float, **kwargs):
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return None
        last = dataframe.iloc[-1]
        bars = (current_time - trade.open_date_utc).total_seconds() / 900
        tag = trade.enter_tag or ""

        if "trend" in tag:
            # Trend trades: hold longer, let 1D support
            is_long = not trade.is_short
            st_against = (is_long and last["st_trend"] == -1) or (not is_long and last["st_trend"] == 1)
            daily_with = (is_long and last.get("st_1d") == 1) or (not is_long and last.get("st_1d") == -1)

            if st_against:
                if daily_with and current_profit > 0.01:
                    return None  # Hold — daily still supports
                return "trend_exit"

            if bars > 150 and 0 < current_profit < 0.005:
                return "trend_time_decay"

        elif "squeeze" in tag:
            # Squeeze trades: faster exits
            if current_profit > 0.015:
                return "squeeze_tp"
            momentum = last.get("momentum", 0)
            if not trade.is_short and momentum < 0:
                return "squeeze_mom_reversal"
            if trade.is_short and momentum > 0:
                return "squeeze_mom_reversal"
            if bars > 60 and current_profit > 0.003:
                return "squeeze_time_tp"
            if bars > 80:
                return "squeeze_time_exit"

        return None

    def leverage(self, pair: str, current_time: datetime, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if len(dataframe) == 0:
            return 2.0

        last = dataframe.iloc[-1]
        regime = last.get("regime", "RANGING")
        adx = last.get("adx", 25)

        if regime in ("TRENDING_BULL", "TRENDING_BEAR"):
            lev = 1.0 + max(adx - 20, 0) * 0.16
            return min(max(lev, 2.0), 5.0)
        elif regime == "ACCUMULATION":
            return 2.0
        return 1.0
