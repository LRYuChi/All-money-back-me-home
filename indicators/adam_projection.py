"""Adam Theory Double Reflection Projection.

Based on J. Welles Wilder's Adam Theory of Markets (1987):
- The best predictor of future price is a mirror reflection of past price.
- Take N past bars, flip in TIME (reverse), flip in PRICE (mirror around current).
- The resulting projection indicates expected future direction and targets.

Usage in Freqtrade:
    from indicators.adam_projection import adam_projection
    dataframe = adam_projection(dataframe, lookback=20)
    # Adds: adam_slope, adam_target_high, adam_target_low, adam_bullish
"""

from __future__ import annotations

import numpy as np
from pandas import DataFrame


def adam_projection(df: DataFrame, lookback: int = 20) -> DataFrame:
    """Calculate Adam Theory double reflection projection.

    For each bar, takes the last `lookback` bars, mirrors them in time and price,
    and computes the projected slope (direction) and target levels.

    Args:
        df: OHLCV DataFrame with columns: open, high, low, close
        lookback: Number of bars to use for projection (default 20)

    Returns:
        DataFrame with added columns:
        - adam_slope: Projected direction (positive=bullish, negative=bearish)
        - adam_target_high: Projected high target (average of projected highs)
        - adam_target_low: Projected low target (average of projected lows)
        - adam_bullish: Boolean, True if projection is bullish
    """
    n = len(df)
    slopes = np.full(n, np.nan)
    target_highs = np.full(n, np.nan)
    target_lows = np.full(n, np.nan)

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    for i in range(lookback, n):
        current_price = close[i]

        # Double reflection:
        # 1. Time flip: reverse the order of past bars
        # 2. Price flip: mirror highs/lows around current price
        #
        # projected_high[j] = 2 * current_price - low[i - j]
        # projected_low[j]  = 2 * current_price - high[i - j]
        #
        # j goes from 1 to lookback (future bars)

        proj_highs = np.empty(lookback)
        proj_lows = np.empty(lookback)

        for j in range(lookback):
            past_idx = i - j - 1  # Reverse time: j=0 → most recent past bar
            if past_idx < 0:
                proj_highs[j] = np.nan
                proj_lows[j] = np.nan
            else:
                proj_highs[j] = 2 * current_price - low[past_idx]
                proj_lows[j] = 2 * current_price - high[past_idx]

        # Projection slope: linear regression of projected midpoints
        proj_mid = (proj_highs + proj_lows) / 2
        valid_mask = ~np.isnan(proj_mid)
        if valid_mask.sum() >= 3:
            x = np.arange(lookback)[valid_mask]
            y = proj_mid[valid_mask]
            # Simple slope via least squares
            x_mean = x.mean()
            y_mean = y.mean()
            slope = np.sum((x - x_mean) * (y - y_mean)) / (np.sum((x - x_mean) ** 2) + 1e-10)
            # Normalize slope as percentage of current price
            slopes[i] = slope / current_price * 100

            # Target: average of first 5 projected bars (near-term target)
            near_term = min(5, int(valid_mask.sum()))
            target_highs[i] = np.nanmean(proj_highs[:near_term])
            target_lows[i] = np.nanmean(proj_lows[:near_term])

    df["adam_slope"] = slopes
    df["adam_target_high"] = target_highs
    df["adam_target_low"] = target_lows
    df["adam_bullish"] = slopes > 0

    return df
