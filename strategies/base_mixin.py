"""Base mixin for shared strategy logic: logging, risk control integration."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class StrategyMixin:
    """Mixin providing shared utilities for Freqtrade strategies.

    Provides:
    - Structured trade logging (inspired by ai-trader BaseStrategy)
    - Risk parameter validation
    - Trade decision audit trail
    """

    # Default risk parameters (override in subclass)
    MAX_LEVERAGE: float = 5.0
    MAX_POSITION_PCT: float = 30.0
    STOPLOSS_PCT: float = -0.02  # -2%

    def log_signal(self, signal_type: str, pair: str, reason: str) -> None:
        """Log a trading signal with structured format."""
        marker = "▲ BUY" if signal_type == "buy" else "▼ SELL"
        logger.info("[%s] %s %s | %s", datetime.now().strftime("%H:%M:%S"), marker, pair, reason)

    def log_trade_result(self, pair: str, profit_pct: float) -> None:
        """Log trade result."""
        marker = "+ PROFIT" if profit_pct > 0 else "- LOSS"
        logger.info("[%s] %s %s | %.2f%%", datetime.now().strftime("%H:%M:%S"), marker, pair, profit_pct * 100)

    def validate_leverage(self, proposed_leverage: float) -> float:
        """Clamp leverage to maximum allowed."""
        return min(proposed_leverage, self.MAX_LEVERAGE)

    def calculate_position_size(
        self,
        balance: float,
        risk_pct: float = 0.02,
        stop_distance_pct: float = 0.02,
        leverage: float = 1.0,
    ) -> float:
        """Calculate position size based on risk percentage.

        Args:
            balance: Account balance in USDT
            risk_pct: Max risk per trade (default 2%)
            stop_distance_pct: Distance to stop loss
            leverage: Leverage multiplier

        Returns:
            Position size in USDT
        """
        risk_amount = balance * risk_pct
        position_size = risk_amount / stop_distance_pct
        max_position = balance * (self.MAX_POSITION_PCT / 100) * leverage
        return min(position_size, max_position)

    def estimate_liquidation_price(
        self,
        entry_price: float,
        leverage: float,
        side: str = "long",
        maintenance_margin_rate: float = 0.004,
    ) -> Optional[float]:
        """Estimate liquidation price for a futures position.

        Args:
            entry_price: Entry price
            leverage: Position leverage
            side: "long" or "short"
            maintenance_margin_rate: Exchange maintenance margin rate

        Returns:
            Estimated liquidation price
        """
        if leverage <= 0:
            return None

        if side == "long":
            return entry_price * (1 - (1 / leverage) + maintenance_margin_rate)
        else:
            return entry_price * (1 + (1 / leverage) - maintenance_margin_rate)
