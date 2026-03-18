"""Bollinger Band / Keltner Channel Squeeze Detector.

.. deprecated::
    This module is superseded by
    ``apps/api/src/strategy/layer2_signal_engine/volatility_indicators.py``
    which is used by all strategy layers. This file is kept for reference only.

Detects volatility compression (squeeze) — when Bollinger Bands contract inside
Keltner Channels, indicating energy buildup before a breakout.

Used by TAHZAN as $f_{Squeeze}$ factor.
"""

from __future__ import annotations

import numpy as np
import talib.abstract as ta
from pandas import DataFrame


def squeeze_detector(df: DataFrame, bb_period: int = 20, bb_std: float = 2.0,
                     kc_period: int = 20, kc_mult: float = 1.5,
                     lookback: int = 100) -> DataFrame:
    """Detect Bollinger Band squeeze and score compression level.

    A squeeze occurs when BB bands are inside Keltner Channel bands,
    meaning volatility is at historically low levels.

    Args:
        df: OHLCV DataFrame
        bb_period: Bollinger Band period
        bb_std: Bollinger Band standard deviation multiplier
        kc_period: Keltner Channel EMA period
        kc_mult: Keltner Channel ATR multiplier
        lookback: Rolling window for BB width percentile

    Returns:
        DataFrame with added columns:
        - squeeze_on: Boolean, True when BB inside KC (squeeze active)
        - squeeze_score: 0.0-1.0, how compressed (lower BB width = higher score)
        - bb_width_pct: BB width as percentile of recent history
    """
    # Bollinger Bands
    bb = ta.BBANDS(df, timeperiod=bb_period, nbdevup=bb_std, nbdevdn=bb_std)
    bb_upper = bb["upperband"]
    bb_lower = bb["lowerband"]
    bb_width = (bb_upper - bb_lower) / ((bb_upper + bb_lower) / 2) * 100

    # Keltner Channel
    kc_mid = ta.EMA(df, timeperiod=kc_period)
    atr = ta.ATR(df, timeperiod=kc_period)
    kc_upper = kc_mid + (atr * kc_mult)
    kc_lower = kc_mid - (atr * kc_mult)

    # Squeeze detection: BB inside KC
    df["squeeze_on"] = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    # BB width percentile (lower = more compressed)
    df["bb_width_pct"] = bb_width.rolling(lookback).apply(
        lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min() + 1e-10) * 100
        if len(x) > 0 else 50
    )

    # Squeeze score: inverted percentile (more compressed = higher score)
    # < 20th percentile → score approaches 1.0
    df["squeeze_score"] = np.clip(1.0 - (df["bb_width_pct"] / 100), 0, 1)

    return df
