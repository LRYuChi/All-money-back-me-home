"""Layer 5 — Backtest-specific data models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..enums import SignalDirection, StrategyName


class BacktestConfig(BaseModel):
    """Configuration for a single backtest run."""

    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    initial_capital: float = 10_000.0
    commission_rate: float = 0.001  # 0.1% per side
    max_risk_pct: float = 0.02  # 2% risk per trade
    max_positions: int = 1
    warmup_bars: int = 200
    leverage: float = 1.0
    max_capital_per_trade: float = 0.5  # 50% of capital


class Position(BaseModel):
    """An open backtest position."""

    id: str
    symbol: str
    direction: SignalDirection
    strategy: StrategyName
    entry_price: float
    entry_time: datetime
    entry_bar: int
    stop_loss: float
    take_profit_levels: list[float] = Field(default_factory=list)
    position_size_usd: float
    leverage: float = 1.0
    confidence: float = 0.0
    reason: str = ""


class TradeRecord(BaseModel):
    """A closed backtest trade with full PnL details."""

    id: str
    symbol: str
    direction: SignalDirection
    strategy: StrategyName
    entry_price: float
    entry_time: datetime
    entry_bar: int
    exit_price: float
    exit_time: datetime
    exit_bar: int
    exit_reason: str
    stop_loss: float
    take_profit_levels: list[float] = Field(default_factory=list)
    position_size_usd: float
    leverage: float = 1.0
    confidence: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    commission_paid: float = 0.0
    duration_bars: int = 0
    r_multiple: float = 0.0


class WalkForwardConfig(BaseModel):
    """Configuration for walk-forward validation."""

    n_splits: int = 5
    train_ratio: float = 0.6
    min_train_bars: int = 500
    min_test_bars: int = 100
