from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .enums import MarketState, SignalDirection, StrategyName, Timeframe


class SwingPoint(BaseModel):
    """A detected swing high or swing low point on a price chart."""

    index: int
    price: float
    ts: datetime
    type: Literal["high", "low"]


class MarketStructureResult(BaseModel):
    """Result of market structure analysis including trend state and CHoCH detection."""

    state: MarketState
    swing_highs: list[SwingPoint] = Field(default_factory=list)
    swing_lows: list[SwingPoint] = Field(default_factory=list)
    choch_detected: bool = False
    choch_direction: SignalDirection | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class MTFAlignment(BaseModel):
    """Multi-timeframe alignment analysis across different chart periods."""

    timeframes: dict[Timeframe, MarketState]
    dominant_direction: MarketState
    alignment_score: float = Field(ge=0.0, le=1.0)


class IndicatorSignal(BaseModel):
    """Signal produced by a single technical indicator."""

    name: str
    value: float | None = None
    signal: SignalDirection
    strength: float = Field(ge=0.0, le=1.0)


class StrategySignal(BaseModel):
    """Composite signal emitted by a trading strategy."""

    strategy: StrategyName
    direction: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit_levels: list[float] = Field(default_factory=list)
    reason_zh: str = ""
    indicators_used: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TradeOrder(BaseModel):
    """A concrete trade order ready for execution."""

    symbol: str
    direction: SignalDirection
    strategy: StrategyName
    entry_price: float
    stop_loss: float
    take_profit_levels: list[float] = Field(default_factory=list)
    position_size: float
    leverage: float = 1.0
    risk_pct: float = Field(default=0.0, ge=0.0, le=1.0)


class BacktestResult(BaseModel):
    """Summary statistics from a strategy backtest run."""

    strategy: StrategyName
    symbol: str
    period: str
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    total_return: float = 0.0
    equity_curve: list[float] = Field(default_factory=list)
    trades: list[dict] = Field(default_factory=list)
    avg_trade_duration_bars: float = 0.0
    calmar_ratio: float = 0.0
    avg_r_multiple: float = 0.0
