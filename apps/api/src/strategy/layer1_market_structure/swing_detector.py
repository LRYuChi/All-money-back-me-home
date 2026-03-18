from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..models import SwingPoint


def _get_timestamp(df: pd.DataFrame, idx: int) -> datetime:
    """Extract timestamp from a DataFrame row by positional index.

    Supports DatetimeIndex or a 'ts' column.
    """
    if isinstance(df.index, pd.DatetimeIndex):
        return df.index[idx].to_pydatetime()
    if "ts" in df.columns:
        val = df.iloc[idx]["ts"]
        if isinstance(val, pd.Timestamp):
            return val.to_pydatetime()
        return val
    raise ValueError("DataFrame must have a DatetimeIndex or a 'ts' column")


def detect_swing_highs(df: pd.DataFrame, lookback: int = 5) -> list[SwingPoint]:
    """Detect swing high points in OHLC price data.

    A swing high at index *i* is identified when ``High[i]`` equals the
    maximum value in the window ``High[i - lookback : i + lookback + 1]``.

    Parameters
    ----------
    df:
        DataFrame with a ``High`` column and either a ``DatetimeIndex`` or
        a ``ts`` column.
    lookback:
        Number of bars to look on each side of the candidate bar.

    Returns
    -------
    list[SwingPoint]
        Detected swing highs sorted by index.
    """
    if "High" not in df.columns:
        raise ValueError("DataFrame must contain a 'High' column")

    highs: list[SwingPoint] = []
    n = len(df)

    for i in range(n):
        window_start = max(0, i - lookback)
        window_end = min(n, i + lookback + 1)
        window = df["High"].iloc[window_start:window_end]

        if df["High"].iloc[i] == window.max():
            # Ensure uniqueness: the candidate must be the *first* occurrence
            # of the max in the window to avoid duplicate swing points when
            # consecutive bars share the same high.
            first_max_pos = int(window.values.argmax()) + window_start
            if first_max_pos == i:
                highs.append(
                    SwingPoint(
                        index=i,
                        price=float(df["High"].iloc[i]),
                        ts=_get_timestamp(df, i),
                        type="high",
                    )
                )

    return highs


def detect_swing_lows(df: pd.DataFrame, lookback: int = 5) -> list[SwingPoint]:
    """Detect swing low points in OHLC price data.

    A swing low at index *i* is identified when ``Low[i]`` equals the
    minimum value in the window ``Low[i - lookback : i + lookback + 1]``.

    Parameters
    ----------
    df:
        DataFrame with a ``Low`` column and either a ``DatetimeIndex`` or
        a ``ts`` column.
    lookback:
        Number of bars to look on each side of the candidate bar.

    Returns
    -------
    list[SwingPoint]
        Detected swing lows sorted by index.
    """
    if "Low" not in df.columns:
        raise ValueError("DataFrame must contain a 'Low' column")

    lows: list[SwingPoint] = []
    n = len(df)

    for i in range(n):
        window_start = max(0, i - lookback)
        window_end = min(n, i + lookback + 1)
        window = df["Low"].iloc[window_start:window_end]

        if df["Low"].iloc[i] == window.min():
            if window.values.argmin() + window_start == i:
                lows.append(
                    SwingPoint(
                        index=i,
                        price=float(df["Low"].iloc[i]),
                        ts=_get_timestamp(df, i),
                        type="low",
                    )
                )

    return lows
