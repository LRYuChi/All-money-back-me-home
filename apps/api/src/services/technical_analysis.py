from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pandas_ta as ta

from src.fetchers.crypto import CryptoFetcher
from src.fetchers.tw_stock import TWStockFetcher
from src.fetchers.us_stock import USStockFetcher
from src.i18n.zh_tw import INDICATOR_NAMES_ZH
from src.models.schemas import (
    AnalysisResult,
    IndicatorResult,
    OHLCVData,
    PatternDetection,
    Signal,
)


def _safe_float(v) -> float | None:
    """Convert a value to float, returning None for NaN/Inf."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


class TechnicalAnalysisService:
    """Core service for computing technical indicators and generating signals."""

    def _get_fetcher(self, market: str):
        if market == "us":
            return USStockFetcher()
        if market == "tw":
            return TWStockFetcher()
        if market == "crypto":
            return CryptoFetcher()
        raise ValueError(f"Unsupported market: {market}")

    def compute_indicators(
        self, df: pd.DataFrame, indicators: list[str]
    ) -> dict[str, IndicatorResult]:
        """Compute requested technical indicators on the given OHLCV DataFrame.

        Supported indicators: sma, rsi, macd, bbands.
        """
        results: dict[str, IndicatorResult] = {}

        if "sma" in indicators:
            sma_20 = ta.sma(df["Close"], length=20)
            results["SMA_20"] = IndicatorResult(
                name=INDICATOR_NAMES_ZH.get("SMA_20", "SMA(20)"),
                values=[_safe_float(v) for v in sma_20],
            )

        if "rsi" in indicators:
            rsi_14 = ta.rsi(df["Close"], length=14)
            results["RSI_14"] = IndicatorResult(
                name=INDICATOR_NAMES_ZH.get("RSI_14", "RSI(14)"),
                values=[_safe_float(v) for v in rsi_14],
            )

        if "macd" in indicators:
            macd_df = ta.macd(df["Close"], fast=12, slow=26, signal=9)
            if macd_df is not None:
                results["MACD"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("MACD", "MACD(12,26,9)"),
                    values=[_safe_float(v) for v in macd_df.iloc[:, 0]],
                )
                results["MACD_signal"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("MACD_signal", "MACD Signal"),
                    values=[_safe_float(v) for v in macd_df.iloc[:, 2]],
                )
                results["MACD_hist"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("MACD_hist", "MACD Histogram"),
                    values=[_safe_float(v) for v in macd_df.iloc[:, 1]],
                )

        if "bbands" in indicators:
            bb = ta.bbands(df["Close"], length=20, std=2.0)
            if bb is not None:
                results["BB_upper"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("BB_upper", "布林通道上軌"),
                    values=[_safe_float(v) for v in bb.iloc[:, 2]],
                )
                results["BB_mid"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("BB_mid", "布林通道中軌"),
                    values=[_safe_float(v) for v in bb.iloc[:, 1]],
                )
                results["BB_lower"] = IndicatorResult(
                    name=INDICATOR_NAMES_ZH.get("BB_lower", "布林通道下軌"),
                    values=[_safe_float(v) for v in bb.iloc[:, 0]],
                )

        return results

    def detect_patterns(self, df: pd.DataFrame) -> list[PatternDetection]:
        """Detect candlestick patterns.

        TODO: Implement pattern detection. ta-lib requires a C library installation
        which is non-trivial on many platforms. For now, returns an empty list.
        Future: integrate with ta-lib or a pure-Python pattern detection library.
        """
        return []

    def generate_signals(
        self, df: pd.DataFrame, indicators: dict[str, IndicatorResult]
    ) -> list[Signal]:
        """Generate basic trading signals by combining RSI and MACD."""
        signals: list[Signal] = []

        if len(df) < 2:
            return signals

        # --- RSI-based signal ---
        rsi_result = indicators.get("RSI_14")
        if rsi_result and rsi_result.values:
            latest_rsi = rsi_result.values[-1]
            if latest_rsi is not None:
                if latest_rsi < 30:
                    signals.append(
                        Signal(
                            type="buy",
                            strength=min(1.0, (30 - latest_rsi) / 30),
                            reason=f"RSI 超賣 ({latest_rsi:.1f} < 30)，可能反彈",
                            indicators=["RSI_14"],
                        )
                    )
                elif latest_rsi > 70:
                    signals.append(
                        Signal(
                            type="sell",
                            strength=min(1.0, (latest_rsi - 70) / 30),
                            reason=f"RSI 超買 ({latest_rsi:.1f} > 70)，可能回調",
                            indicators=["RSI_14"],
                        )
                    )

        # --- MACD crossover signal ---
        macd_result = indicators.get("MACD")
        macd_signal_result = indicators.get("MACD_signal")
        if macd_result and macd_signal_result:
            macd_vals = macd_result.values
            sig_vals = macd_signal_result.values
            if len(macd_vals) >= 2 and len(sig_vals) >= 2:
                curr_macd = macd_vals[-1]
                prev_macd = macd_vals[-2]
                curr_sig = sig_vals[-1]
                prev_sig = sig_vals[-2]

                if all(v is not None for v in [curr_macd, prev_macd, curr_sig, prev_sig]):
                    # Bullish crossover: MACD crosses above signal
                    if prev_macd <= prev_sig and curr_macd > curr_sig:
                        signals.append(
                            Signal(
                                type="buy",
                                strength=0.6,
                                reason="MACD 金叉：MACD 線上穿信號線，短期動能轉強",
                                indicators=["MACD", "MACD_signal"],
                            )
                        )
                    # Bearish crossover: MACD crosses below signal
                    elif prev_macd >= prev_sig and curr_macd < curr_sig:
                        signals.append(
                            Signal(
                                type="sell",
                                strength=0.6,
                                reason="MACD 死叉：MACD 線下穿信號線，短期動能轉弱",
                                indicators=["MACD", "MACD_signal"],
                            )
                        )

        # If no directional signals, default to hold
        if not signals:
            signals.append(
                Signal(
                    type="hold",
                    strength=0.5,
                    reason="目前無明顯買賣訊號，建議觀望",
                    indicators=[],
                )
            )

        return signals

    def _build_summary(self, signals: list[Signal], indicators: dict[str, IndicatorResult]) -> str:
        """Build a Traditional Chinese summary of the analysis."""
        parts: list[str] = []

        rsi = indicators.get("RSI_14")
        if rsi and rsi.values and rsi.values[-1] is not None:
            parts.append(f"RSI(14) 目前為 {rsi.values[-1]:.1f}")

        macd_hist = indicators.get("MACD_hist")
        if macd_hist and macd_hist.values and macd_hist.values[-1] is not None:
            direction = "正值（多方動能）" if macd_hist.values[-1] > 0 else "負值（空方動能）"
            parts.append(f"MACD 柱狀圖為{direction}")

        for sig in signals:
            parts.append(sig.reason)

        return "；".join(parts) if parts else "分析完成，無特殊訊號。"

    def analyze(
        self,
        symbol: str,
        market: str,
        interval: str = "1d",
        period: str = "6mo",
        indicators: list[str] | None = None,
    ) -> AnalysisResult:
        """Full analysis pipeline: fetch data -> compute indicators -> detect patterns -> generate signals."""
        if indicators is None:
            indicators = ["sma", "rsi", "macd", "bbands"]

        fetcher = self._get_fetcher(market)
        df = fetcher.fetch_ohlcv(symbol, interval=interval, period=period)

        if df.empty:
            return AnalysisResult(
                symbol=symbol,
                market=market,
                name_zh="",
                interval=interval,
                ohlcv=[],
                indicators=[],
                patterns=[],
                signals=[
                    Signal(
                        type="hold",
                        strength=0.0,
                        reason="無法取得資料，無法進行分析",
                        indicators=[],
                    )
                ],
                summary_zh="無法取得市場資料。",
            )

        # Build OHLCV response
        ohlcv_list: list[OHLCVData] = []
        for idx, row in df.iterrows():
            ts = idx if isinstance(idx, datetime) else datetime.now(tz=timezone.utc)
            ohlcv_list.append(
                OHLCVData(
                    ts=ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0)),
                )
            )

        computed = self.compute_indicators(df, indicators)
        patterns = self.detect_patterns(df)
        signals = self.generate_signals(df, computed)
        summary = self._build_summary(signals, computed)

        return AnalysisResult(
            symbol=symbol,
            market=market,
            name_zh="",
            interval=interval,
            ohlcv=ohlcv_list,
            indicators=list(computed.values()),
            patterns=patterns,
            signals=signals,
            summary_zh=summary,
        )
