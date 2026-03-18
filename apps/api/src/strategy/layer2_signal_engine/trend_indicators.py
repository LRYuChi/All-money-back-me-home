"""Layer 2 — Trend indicators using pandas_ta.

Provides EMA stack, ADX, Keltner Channel computations and a unified
``evaluate_trend_signals`` entry-point that returns a list of
:class:`IndicatorSignal`.
"""

from __future__ import annotations

import pandas as pd
import pandas_ta as ta

from strategy.enums import SignalDirection
from strategy.models import IndicatorSignal


def compute_ema_stack(
    df: pd.DataFrame,
    periods: list[int] | None = None,
) -> dict[str, pd.Series]:
    """Compute multiple EMAs.

    Returns a dict like ``{"EMA_9": Series, "EMA_21": Series, ...}``.
    """
    if periods is None:
        periods = [9, 21, 55, 200]

    result: dict[str, pd.Series] = {}
    for p in periods:
        ema = ta.ema(df["Close"], length=p)
        if ema is not None:
            result[f"EMA_{p}"] = ema
    return result


def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute ADX and return the ADX series."""
    adx_df = ta.adx(df["High"], df["Low"], df["Close"], length=period)
    if adx_df is not None and f"ADX_{period}" in adx_df.columns:
        return adx_df[f"ADX_{period}"]
    return pd.Series(dtype=float)


def compute_keltner_channel(
    df: pd.DataFrame,
    ema_period: int = 20,
    atr_mult: float = 2.0,
) -> dict[str, pd.Series]:
    """Compute Keltner Channel (EMA +/- multiplier * ATR).

    Returns ``{"upper": Series, "mid": Series, "lower": Series}``.
    """
    ema = ta.ema(df["Close"], length=ema_period)
    atr = ta.atr(df["High"], df["Low"], df["Close"], length=ema_period)

    empty = pd.Series(dtype=float)
    if ema is None or atr is None:
        return {"upper": empty, "mid": empty, "lower": empty}

    return {
        "upper": ema + atr_mult * atr,
        "mid": ema,
        "lower": ema - atr_mult * atr,
    }


def evaluate_trend_signals(df: pd.DataFrame) -> list[IndicatorSignal]:
    """Evaluate all trend indicators and return a list of signals."""
    signals: list[IndicatorSignal] = []

    # --- EMA alignment ---------------------------------------------------
    emas = compute_ema_stack(df)
    required_keys = ["EMA_9", "EMA_21", "EMA_55"]
    if all(k in emas for k in required_keys):
        last_9 = emas["EMA_9"].iloc[-1]
        last_21 = emas["EMA_21"].iloc[-1]
        last_55 = emas["EMA_55"].iloc[-1]

        if not (pd.notna(last_9) and pd.notna(last_21) and pd.notna(last_55)):
            # Insufficient data — skip EMA signal
            pass
        elif last_9 > last_21 > last_55:
            signals.append(
                IndicatorSignal(
                    name="EMA_Stack",
                    value=None,
                    signal=SignalDirection.LONG,
                    strength=0.8,
                )
            )
        elif last_9 < last_21 < last_55:
            signals.append(
                IndicatorSignal(
                    name="EMA_Stack",
                    value=None,
                    signal=SignalDirection.SHORT,
                    strength=0.8,
                )
            )
        else:
            signals.append(
                IndicatorSignal(
                    name="EMA_Stack",
                    value=None,
                    signal=SignalDirection.NEUTRAL,
                    strength=0.3,
                )
            )

    # --- ADX (trend strength, direction-agnostic) -------------------------
    adx = compute_adx(df)
    if len(adx) > 0:
        last_adx = adx.iloc[-1]
        if pd.notna(last_adx):
            if last_adx > 40:
                strength = 0.9
            elif last_adx > 25:
                strength = 0.7
            else:
                strength = 0.3
            signals.append(
                IndicatorSignal(
                    name="ADX",
                    value=float(last_adx),
                    signal=SignalDirection.NEUTRAL,
                    strength=strength,
                )
            )

    # --- Keltner Channel position -----------------------------------------
    kc = compute_keltner_channel(df)
    if len(kc["upper"]) > 0:
        close = df["Close"].iloc[-1]
        kc_upper = kc["upper"].iloc[-1]
        kc_lower = kc["lower"].iloc[-1]
        kc_mid = kc["mid"].iloc[-1]

        if pd.notna(kc_upper) and pd.notna(kc_lower):
            if close > kc_upper:
                signals.append(
                    IndicatorSignal(
                        name="Keltner",
                        value=float(close),
                        signal=SignalDirection.LONG,
                        strength=0.7,
                    )
                )
            elif close < kc_lower:
                signals.append(
                    IndicatorSignal(
                        name="Keltner",
                        value=float(close),
                        signal=SignalDirection.SHORT,
                        strength=0.7,
                    )
                )
            else:
                # Normalise position inside channel to strength
                channel_width = kc_upper - kc_lower
                if channel_width > 0:
                    position = (close - kc_mid) / (channel_width / 2)
                    direction = (
                        SignalDirection.LONG if position > 0 else SignalDirection.SHORT
                    )
                    signals.append(
                        IndicatorSignal(
                            name="Keltner",
                            value=float(close),
                            signal=direction,
                            strength=round(min(abs(position), 1.0), 2),
                        )
                    )

    return signals
