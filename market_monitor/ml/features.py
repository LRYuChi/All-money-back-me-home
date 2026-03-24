"""Feature engineering for market prediction models.

Computes ~60 features from OHLCV + macro data.
All features are pure numpy/pandas (no talib dependency).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger(__name__)


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, min_periods=span).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
    atr = _atr(high, low, close, period)
    plus_di = 100 * plus_dm.rolling(period).mean() / atr.replace(0, 1e-10)
    minus_di = 100 * minus_dm.rolling(period).mean() / atr.replace(0, 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1e-10)
    return dx.rolling(period).mean()


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _bbands(close: pd.Series, period: int = 20, nbdev: float = 2.0):
    middle = _sma(close, period)
    std = close.rolling(period).std()
    upper = middle + nbdev * std
    lower = middle - nbdev * std
    return upper, middle, lower


def compute_features(df: pd.DataFrame, macro: dict | None = None) -> pd.DataFrame:
    """Compute ~60 features from OHLCV dataframe.

    Args:
        df: DataFrame with columns [open, high, low, close, volume]
        macro: Optional dict with {sp500_roc5, sox_roc5, vix, dxy, twd, tnx, gold_roc5, btc_roc5}

    Returns:
        DataFrame with feature columns (NaN rows at start are expected)
    """
    feat = pd.DataFrame(index=df.index)
    c = df["close"]
    h = df["high"]
    l = df["low"]
    o = df["open"]
    v = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

    # ===== PRICE TECHNICAL (20) =====

    # RSI
    feat["rsi_14"] = _rsi(c, 14)
    feat["rsi_7"] = _rsi(c, 7)

    # MACD
    macd_line, signal_line, macd_hist = _macd(c)
    feat["macd_hist"] = macd_hist
    feat["macd_cross"] = (macd_line > signal_line).astype(int)

    # ADX
    feat["adx_14"] = _adx(h, l, c, 14)

    # Bollinger Bands
    bb_upper, bb_middle, bb_lower = _bbands(c, 20)
    feat["bb_pctb"] = (c - bb_lower) / (bb_upper - bb_lower).replace(0, 1e-10)
    feat["bb_width"] = (bb_upper - bb_lower) / bb_middle.replace(0, 1e-10)

    # MA distance ratios
    for p in [20, 60, 120]:
        ma = _sma(c, p)
        feat[f"ma{p}_dist"] = (c / ma - 1) * 100

    # ATR normalized
    atr = _atr(h, l, c, 14)
    feat["atr_pct"] = atr / c * 100

    # Candle structure
    body = (c - o).abs()
    full_range = (h - l).replace(0, 1e-10)
    feat["body_pct"] = body / full_range
    feat["upper_wick"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / full_range
    feat["lower_wick"] = (pd.concat([c, o], axis=1).min(axis=1) - l) / full_range

    # Distance from N-day high/low
    feat["dist_high_20"] = (c / h.rolling(20).max() - 1) * 100
    feat["dist_low_20"] = (c / l.rolling(20).min() - 1) * 100

    # Supertrend direction (simplified)
    st_upper = (h + l) / 2 - 3.0 * atr
    feat["above_st"] = (c > st_upper).astype(int)

    # ===== MOMENTUM (10) =====

    feat["roc_5"] = c.pct_change(5) * 100
    feat["roc_10"] = c.pct_change(10) * 100
    feat["roc_20"] = c.pct_change(20) * 100

    # Momentum alignment (all 3 ROCs same direction)
    feat["mom_align"] = (
        (feat["roc_5"] > 0).astype(int)
        + (feat["roc_10"] > 0).astype(int)
        + (feat["roc_20"] > 0).astype(int)
    )

    # Acceleration
    feat["roc_accel"] = feat["roc_5"].diff(5)

    # Volume
    vol_ma5 = _sma(v, 5)
    vol_ma20 = _sma(v, 20)
    feat["vol_ratio_5_20"] = vol_ma5 / vol_ma20.replace(0, 1e-10)
    feat["vol_roc"] = v.pct_change(5) * 100

    # OBV direction
    obv = (v * np.sign(c.diff())).cumsum()
    feat["obv_slope"] = obv.diff(5)

    # Price-volume divergence (price up but volume down = bearish)
    feat["pv_diverge"] = (feat["roc_5"] * feat["vol_roc"]).apply(lambda x: -1 if x < 0 else 1)

    # ===== MACRO / INTERNATIONAL (10) =====
    if macro:
        for key in ["sp500_roc5", "sox_roc5", "vix", "vix_chg", "dxy", "twd_chg",
                     "btc_roc5", "tnx", "gold_roc5"]:
            feat[f"macro_{key}"] = macro.get(key, 0)
    else:
        # Fill with zeros if no macro data
        for key in ["sp500_roc5", "sox_roc5", "vix", "vix_chg", "dxy", "twd_chg",
                     "btc_roc5", "tnx", "gold_roc5"]:
            feat[f"macro_{key}"] = 0

    # ===== TIME FEATURES (5) =====
    if hasattr(df.index, 'dayofweek'):
        feat["dow"] = df.index.dayofweek
        feat["month_sin"] = np.sin(2 * np.pi * df.index.month / 12)
        feat["month_cos"] = np.cos(2 * np.pi * df.index.month / 12)
    elif "date" in df.columns:
        dates = pd.to_datetime(df["date"])
        feat["dow"] = dates.dt.dayofweek
        feat["month_sin"] = np.sin(2 * np.pi * dates.dt.month / 12)
        feat["month_cos"] = np.cos(2 * np.pi * dates.dt.month / 12)

    return feat


def create_labels(df: pd.DataFrame, horizons: list[int] = [5, 20, 60],
                  threshold: float = 0.02) -> pd.DataFrame:
    """Create multi-horizon classification labels.

    Labels: 0=DOWN, 1=FLAT, 2=UP

    Args:
        df: DataFrame with 'close' column
        horizons: List of forward-looking periods
        threshold: % threshold for UP/DOWN classification
    """
    labels = pd.DataFrame(index=df.index)
    c = df["close"]

    for h in horizons:
        future_ret = c.shift(-h) / c - 1
        labels[f"label_{h}"] = np.where(
            future_ret > threshold, 2,   # UP
            np.where(future_ret < -threshold, 0, 1)  # DOWN / FLAT
        )
        labels[f"ret_{h}"] = future_ret

    return labels
