"""Layer 3 — BB Squeeze Strategy (Strategy B).

Detects Bollinger Band squeeze releases and generates trade signals based on
the breakout direction.  A squeeze occurs when BB sit inside the Keltner
Channel, indicating low volatility.  When the squeeze releases, a breakout
in the direction of momentum is expected.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from strategy.enums import SignalDirection, StrategyName
from strategy.models import StrategySignal
from strategy.layer2_signal_engine.volatility_indicators import (
    compute_bb_squeeze,
    compute_bollinger_bands,
    detect_squeeze_release,
)
from strategy.layer2_signal_engine.trend_indicators import compute_ema_stack


class BBSqueezeStrategy:
    """BB Squeeze breakout strategy.

    Entry logic:
    1. Detect that a BB squeeze has been active for at least ``min_squeeze_bars``.
    2. On squeeze release, determine direction from close vs BB mid.
    3. Confirm direction using short EMA alignment (EMA_9 vs EMA_21).
    4. Emit a :class:`StrategySignal` with entry, stop-loss, and TP levels.
    """

    def __init__(
        self,
        bb_length: int = 20,
        bb_std: float = 2.0,
        kc_length: int = 20,
        kc_mult: float = 1.5,
        min_squeeze_bars: int = 3,
        atr_sl_mult: float = 1.5,
        rr_ratios: list[float] | None = None,
    ) -> None:
        self.bb_length = bb_length
        self.bb_std = bb_std
        self.kc_length = kc_length
        self.kc_mult = kc_mult
        self.min_squeeze_bars = min_squeeze_bars
        self.atr_sl_mult = atr_sl_mult
        self.rr_ratios = rr_ratios or [1.5, 2.5, 3.5]

    def evaluate(self, df: pd.DataFrame) -> StrategySignal | None:
        """Run the BB Squeeze strategy on the given OHLCV DataFrame.

        Parameters
        ----------
        df:
            DataFrame with Open, High, Low, Close, Volume columns and a
            DatetimeIndex.

        Returns
        -------
        StrategySignal | None
            A signal if a squeeze release is detected, otherwise ``None``.
        """
        if len(df) < self.bb_length + self.min_squeeze_bars + 5:
            return None

        squeeze = compute_bb_squeeze(
            df,
            bb_length=self.bb_length,
            bb_std=self.bb_std,
            kc_length=self.kc_length,
            kc_mult=self.kc_mult,
        )

        if len(squeeze) == 0:
            return None

        released = detect_squeeze_release(squeeze, lookback=self.min_squeeze_bars)
        if not released:
            return None

        # Determine direction from close vs BB midline
        bb = compute_bollinger_bands(df, length=self.bb_length, std=self.bb_std)
        mid = bb["mid"]
        if len(mid) == 0 or pd.isna(mid.iloc[-1]):
            return None

        close = df["Close"].iloc[-1]
        bb_mid = mid.iloc[-1]

        direction = SignalDirection.LONG if close > bb_mid else SignalDirection.SHORT

        # Confirm with EMA alignment
        emas = compute_ema_stack(df, periods=[9, 21])
        if "EMA_9" in emas and "EMA_21" in emas:
            ema9 = emas["EMA_9"].iloc[-1]
            ema21 = emas["EMA_21"].iloc[-1]
            if pd.notna(ema9) and pd.notna(ema21):
                ema_direction = SignalDirection.LONG if ema9 > ema21 else SignalDirection.SHORT
                if ema_direction != direction:
                    confidence = 0.55
                else:
                    confidence = 0.80
            else:
                confidence = 0.60
        else:
            confidence = 0.60

        # Compute stop-loss and take-profit levels
        entry_price = float(close)
        atr_series = pd.Series(dtype=float)
        try:
            import pandas_ta as ta

            atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        except Exception:
            pass

        if atr_series is not None and len(atr_series) > 0 and pd.notna(atr_series.iloc[-1]):
            atr_val = float(atr_series.iloc[-1])
        else:
            atr_val = float((df["High"].iloc[-20:] - df["Low"].iloc[-20:]).mean())

        if direction == SignalDirection.LONG:
            stop_loss = entry_price - self.atr_sl_mult * atr_val
            take_profit_levels = [
                round(entry_price + rr * atr_val, 8) for rr in self.rr_ratios
            ]
        else:
            stop_loss = entry_price + self.atr_sl_mult * atr_val
            take_profit_levels = [
                round(entry_price - rr * atr_val, 8) for rr in self.rr_ratios
            ]

        reason_zh = (
            "布林擠壓釋放 — 波動率從低位擴張，"
            + ("向上突破中軌" if direction == SignalDirection.LONG else "向下跌破中軌")
        )

        return StrategySignal(
            strategy=StrategyName.BB_SQUEEZE,
            direction=direction,
            confidence=confidence,
            entry_price=round(entry_price, 8),
            stop_loss=round(stop_loss, 8),
            take_profit_levels=take_profit_levels,
            reason_zh=reason_zh,
            indicators_used=["BB_Squeeze", "Keltner", "EMA_9", "EMA_21"],
            timestamp=datetime.now(tz=timezone.utc),
        )
