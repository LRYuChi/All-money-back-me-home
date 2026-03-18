"""Layer 2 — Volatility indicators using pandas_ta.

Provides ATR, Bollinger Bands, BB Width, BB Squeeze detection,
squeeze-release detection, and a unified ``evaluate_volatility_signals``
entry-point.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from strategy.enums import SignalDirection
from strategy.models import IndicatorSignal

from .trend_indicators import compute_keltner_channel


# ---------------------------------------------------------------------------
# Core computations
# ---------------------------------------------------------------------------

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=period)
    if atr is not None and len(atr) > 0:
        return atr
    return pd.Series(dtype=float)


def compute_bollinger_bands(
    df: pd.DataFrame,
    length: int = 20,
    std: float = 2.0,
) -> dict[str, pd.Series]:
    """Compute Bollinger Bands.

    Returns ``{"upper": Series, "mid": Series, "lower": Series}``.
    """
    bb = ta.bbands(df["Close"], length=length, std=std)
    empty = pd.Series(dtype=float)

    if bb is None:
        return {"upper": empty, "mid": empty, "lower": empty}

    # pandas_ta column naming convention: BBU_<length>_<std>, BBM_..., BBL_...
    suffix = f"{length}_{std}"
    upper_col = f"BBU_{suffix}"
    mid_col = f"BBM_{suffix}"
    lower_col = f"BBL_{suffix}"

    return {
        "upper": bb[upper_col] if upper_col in bb.columns else empty,
        "mid": bb[mid_col] if mid_col in bb.columns else empty,
        "lower": bb[lower_col] if lower_col in bb.columns else empty,
    }


def compute_bb_width(
    df: pd.DataFrame,
    length: int = 20,
    std: float = 2.0,
) -> pd.Series:
    """Compute Bollinger Band width: (upper - lower) / mid."""
    bands = compute_bollinger_bands(df, length=length, std=std)
    upper = bands["upper"]
    lower = bands["lower"]
    mid = bands["mid"]

    if len(upper) == 0 or len(mid) == 0:
        return pd.Series(dtype=float)

    # Avoid division by zero
    width = (upper - lower) / mid.clip(lower=1e-10)
    return width


def compute_bb_squeeze(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_std: float = 2.0,
    kc_length: int = 20,
    kc_mult: float = 1.5,
) -> pd.Series:
    """Detect BB Squeeze: True when Bollinger Bands sit INSIDE Keltner Channel.

    Squeeze ON  = BB_upper < KC_upper AND BB_lower > KC_lower
    Squeeze OFF = otherwise (bands have expanded back outside KC)
    """
    bb = compute_bollinger_bands(df, length=bb_length, std=bb_std)
    kc = compute_keltner_channel(df, ema_period=kc_length, atr_mult=kc_mult)

    bb_upper = bb["upper"]
    bb_lower = bb["lower"]
    kc_upper = kc["upper"]
    kc_lower = kc["lower"]

    if len(bb_upper) == 0 or len(kc_upper) == 0:
        return pd.Series(dtype=bool)

    squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    return squeeze.astype(bool)


def detect_squeeze_release(squeeze: pd.Series, lookback: int = 3) -> bool:
    """Return True if squeeze was on for >= *lookback* bars and just released.

    "Just released" means the most-recent bar is False (squeeze off) while the
    preceding bar was True (squeeze on), and the squeeze was active for at
    least *lookback* consecutive bars before the release.
    """
    if len(squeeze) < lookback + 1:
        return False

    # Last bar must be squeeze-OFF and prior bar squeeze-ON
    if squeeze.iloc[-1] or not squeeze.iloc[-2]:
        return False

    # Count consecutive True bars ending at iloc[-2]
    consecutive = 0
    for i in range(len(squeeze) - 2, -1, -1):
        if squeeze.iloc[i]:
            consecutive += 1
        else:
            break

    return consecutive >= lookback


# ---------------------------------------------------------------------------
# Unified evaluator
# ---------------------------------------------------------------------------

def evaluate_volatility_signals(df: pd.DataFrame) -> list[IndicatorSignal]:
    """Evaluate all volatility indicators and return a list of signals."""
    signals: list[IndicatorSignal] = []

    # --- ATR (normalised to close for relative volatility) ----------------
    atr = compute_atr(df)
    if len(atr) > 0:
        last_atr = atr.iloc[-1]
        last_close = df["Close"].iloc[-1]
        if pd.notna(last_atr) and last_close > 0:
            relative_atr = last_atr / last_close
            # High relative ATR (>3 %) = elevated volatility
            if relative_atr > 0.03:
                strength = 0.9
            elif relative_atr > 0.015:
                strength = 0.6
            else:
                strength = 0.3
            signals.append(
                IndicatorSignal(
                    name="ATR",
                    value=float(last_atr),
                    signal=SignalDirection.NEUTRAL,
                    strength=strength,
                )
            )

    # --- BB Width (expansion/contraction) ---------------------------------
    bb_width = compute_bb_width(df)
    if len(bb_width) > 20:
        last_width = bb_width.iloc[-1]
        avg_width = bb_width.iloc[-60:].mean() if len(bb_width) >= 60 else bb_width.mean()
        if pd.notna(last_width) and pd.notna(avg_width) and avg_width > 0:
            ratio = last_width / avg_width
            if ratio < 0.6:
                # Very compressed — squeeze likely
                signals.append(
                    IndicatorSignal(
                        name="BB_Width",
                        value=float(last_width),
                        signal=SignalDirection.NEUTRAL,
                        strength=0.9,
                    )
                )
            elif ratio < 0.85:
                signals.append(
                    IndicatorSignal(
                        name="BB_Width",
                        value=float(last_width),
                        signal=SignalDirection.NEUTRAL,
                        strength=0.6,
                    )
                )
            else:
                signals.append(
                    IndicatorSignal(
                        name="BB_Width",
                        value=float(last_width),
                        signal=SignalDirection.NEUTRAL,
                        strength=0.3,
                    )
                )

    # --- BB Squeeze status ------------------------------------------------
    squeeze = compute_bb_squeeze(df)
    if len(squeeze) > 0:
        is_on = bool(squeeze.iloc[-1])
        released = detect_squeeze_release(squeeze, lookback=3)

        if released:
            # Determine breakout direction from close vs BB mid
            bb = compute_bollinger_bands(df)
            mid = bb["mid"]
            if len(mid) > 0 and pd.notna(mid.iloc[-1]):
                direction = (
                    SignalDirection.LONG
                    if df["Close"].iloc[-1] > mid.iloc[-1]
                    else SignalDirection.SHORT
                )
            else:
                direction = SignalDirection.NEUTRAL

            signals.append(
                IndicatorSignal(
                    name="BB_Squeeze_Release",
                    value=None,
                    signal=direction,
                    strength=0.85,
                )
            )
        elif is_on:
            signals.append(
                IndicatorSignal(
                    name="BB_Squeeze_Active",
                    value=None,
                    signal=SignalDirection.NEUTRAL,
                    strength=0.5,
                )
            )

    return signals
