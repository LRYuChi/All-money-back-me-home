from __future__ import annotations

import math


class RiskManagementService:
    """Risk management calculations for position sizing and performance metrics."""

    def calculate_stop_loss(
        self, entry_price: float, atr: float, multiplier: float = 2.0
    ) -> float:
        """Calculate stop-loss price using ATR-based method.

        Args:
            entry_price: The entry price of the position.
            atr: Average True Range value.
            multiplier: ATR multiplier (default 2.0).

        Returns:
            Stop-loss price level.
        """
        return entry_price - (atr * multiplier)

    def calculate_take_profit(
        self, entry_price: float, atr: float, risk_reward_ratio: float = 2.0
    ) -> float:
        """Calculate take-profit price using ATR and risk/reward ratio.

        Args:
            entry_price: The entry price of the position.
            atr: Average True Range value.
            risk_reward_ratio: Desired risk/reward ratio (default 2.0).

        Returns:
            Take-profit price level.
        """
        risk = atr * 2.0  # default stop distance is 2x ATR
        return entry_price + (risk * risk_reward_ratio)

    def calculate_position_size(
        self,
        capital: float,
        risk_percent: float,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        """Calculate the number of shares/units to buy based on risk parameters.

        Args:
            capital: Total available capital.
            risk_percent: Percentage of capital willing to risk (e.g. 0.02 for 2%).
            entry_price: Planned entry price.
            stop_loss: Planned stop-loss price.

        Returns:
            Number of shares/units (floored to integer for stocks).
        """
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit == 0:
            return 0.0

        risk_amount = capital * risk_percent
        return math.floor(risk_amount / risk_per_unit)

    def calculate_max_drawdown(self, equity_curve: list[float]) -> float:
        """Calculate the maximum drawdown from an equity curve.

        Args:
            equity_curve: List of portfolio equity values over time.

        Returns:
            Maximum drawdown as a positive decimal (e.g. 0.15 for 15% drawdown).
        """
        if len(equity_curve) < 2:
            return 0.0

        peak = equity_curve[0]
        max_dd = 0.0

        for value in equity_curve:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak if peak > 0 else 0.0
            if drawdown > max_dd:
                max_dd = drawdown

        return max_dd

    def calculate_sharpe_ratio(
        self, returns: list[float], risk_free_rate: float = 0.0
    ) -> float:
        """Calculate the annualized Sharpe ratio.

        Args:
            returns: List of periodic returns (e.g. daily returns as decimals).
            risk_free_rate: Risk-free rate for the same period (default 0.0).

        Returns:
            Annualized Sharpe ratio. Returns 0.0 if insufficient data.
        """
        if len(returns) < 2:
            return 0.0

        excess = [r - risk_free_rate for r in returns]
        mean_excess = sum(excess) / len(excess)
        variance = sum((r - mean_excess) ** 2 for r in excess) / (len(excess) - 1)
        std_dev = math.sqrt(variance)

        if std_dev == 0:
            return 0.0

        # Annualize assuming 252 trading days
        return (mean_excess / std_dev) * math.sqrt(252)
