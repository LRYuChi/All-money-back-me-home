from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class OHLCVData(BaseModel):
    """Single OHLCV candlestick bar."""

    ts: str = Field(description="ISO-8601 timestamp")
    open: float
    high: float
    low: float
    close: float
    volume: float


class IndicatorResult(BaseModel):
    """Result for a single technical indicator."""

    name: str = Field(description="Indicator name (e.g. SMA_20, RSI_14)")
    values: list[float | None] = Field(
        description="Indicator values aligned with OHLCV data (None for initial NaN periods)"
    )


class PatternDetection(BaseModel):
    """A detected candlestick or chart pattern."""

    name: str = Field(description="Pattern name in English")
    name_zh: str = Field(description="Pattern name in Traditional Chinese")
    date: str = Field(description="ISO-8601 date when the pattern was detected")
    direction: Literal["bullish", "bearish", "neutral"] = Field(
        description="Directional bias of the pattern"
    )


class Signal(BaseModel):
    """A trading signal derived from technical analysis."""

    type: Literal["buy", "sell", "hold"]
    strength: float = Field(ge=0.0, le=1.0, description="Signal strength from 0 to 1")
    reason: str = Field(description="Human-readable explanation of the signal")
    indicators: list[str] = Field(description="Indicators contributing to this signal")


class AnalysisResult(BaseModel):
    """Complete technical analysis result for a symbol."""

    symbol: str
    market: str
    name_zh: str = Field(default="", description="Symbol name in Traditional Chinese")
    interval: str
    ohlcv: list[OHLCVData]
    indicators: list[IndicatorResult]
    patterns: list[PatternDetection]
    signals: list[Signal]
    summary_zh: str = Field(default="", description="Analysis summary in Traditional Chinese")
