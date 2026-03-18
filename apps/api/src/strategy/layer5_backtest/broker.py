"""Layer 5 — SimulatedBroker: virtual portfolio and position management."""

from __future__ import annotations

from datetime import datetime

from ..enums import SignalDirection, StrategyName
from .models import BacktestConfig, Position, TradeRecord


class SimulatedBroker:
    """Manages virtual capital, open positions, and trade history.

    Mirrors the logic in ``hourly_scanner.py`` but designed for backtest loops.
    SL/TP checks use candle High/Low for realistic intra-bar detection.
    When both SL and TP could trigger on the same bar, SL takes priority
    (conservative / anti-fragile).
    """

    def __init__(self, config: BacktestConfig) -> None:
        self._config = config
        self.capital: float = config.initial_capital
        self.open_positions: list[Position] = []
        self.closed_trades: list[TradeRecord] = []
        self.equity_curve: list[float] = []
        self._trade_counter: int = 0

    def open_position(
        self,
        symbol: str,
        direction: SignalDirection,
        entry_price: float,
        stop_loss: float,
        take_profit_levels: list[float],
        strategy: StrategyName,
        bar_index: int,
        timestamp: datetime,
        confidence: float = 0.0,
        reason: str = "",
    ) -> Position | None:
        """Open a new position if constraints allow."""
        # Already have position in this symbol?
        if any(p.symbol == symbol for p in self.open_positions):
            return None
        # Max positions reached?
        if len(self.open_positions) >= self._config.max_positions:
            return None

        # Risk-based position sizing
        risk_per_unit = abs(entry_price - stop_loss) / entry_price
        if risk_per_unit < 0.001:  # < 0.1% distance = too tight, skip
            return None

        risk_amount = self.capital * self._config.max_risk_pct
        position_size_usd = min(
            risk_amount / risk_per_unit,
            self.capital * self._config.max_capital_per_trade,
        )

        # Deduct open commission
        commission = position_size_usd * self._config.commission_rate
        if position_size_usd + commission > self.capital:
            return None

        self._trade_counter += 1
        pos = Position(
            id=f"BT_{self._trade_counter:04d}",
            symbol=symbol,
            direction=direction,
            strategy=strategy,
            entry_price=entry_price,
            entry_time=timestamp,
            entry_bar=bar_index,
            stop_loss=stop_loss,
            take_profit_levels=take_profit_levels,
            position_size_usd=position_size_usd,
            leverage=self._config.leverage,
            confidence=confidence,
            reason=reason,
        )
        self.open_positions.append(pos)
        return pos

    def check_positions(
        self,
        high: float,
        low: float,
        close: float,
        bar_index: int,
        timestamp: datetime,
    ) -> list[TradeRecord]:
        """Check open positions for SL/TP hits using candle High/Low."""
        closed: list[TradeRecord] = []

        for pos in list(self.open_positions):
            exit_price: float | None = None
            exit_reason = ""

            if pos.direction == SignalDirection.LONG:
                sl_hit = low <= pos.stop_loss
                tp_hit = pos.take_profit_levels and high >= pos.take_profit_levels[0]

                if sl_hit:
                    exit_price = pos.stop_loss
                    exit_reason = "止損觸發"
                elif tp_hit:
                    exit_price = pos.take_profit_levels[0]
                    exit_reason = f"止盈觸發 ({pos.take_profit_levels[0]:.2f})"

            elif pos.direction == SignalDirection.SHORT:
                sl_hit = high >= pos.stop_loss
                tp_hit = pos.take_profit_levels and low <= pos.take_profit_levels[0]

                if sl_hit:
                    exit_price = pos.stop_loss
                    exit_reason = "止損觸發"
                elif tp_hit:
                    exit_price = pos.take_profit_levels[0]
                    exit_reason = f"止盈觸發 ({pos.take_profit_levels[0]:.2f})"

            if exit_price is not None:
                trade = self._close_position(
                    pos, exit_price, exit_reason, bar_index, timestamp
                )
                closed.append(trade)

        return closed

    def _close_position(
        self,
        position: Position,
        exit_price: float,
        reason: str,
        bar_index: int,
        timestamp: datetime,
    ) -> TradeRecord:
        """Close a position and record the trade."""
        if position.direction == SignalDirection.LONG:
            pnl_pct = (exit_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - exit_price) / position.entry_price

        pnl_usd_gross = position.position_size_usd * pnl_pct
        # Commission on both open and close
        total_commission = position.position_size_usd * self._config.commission_rate * 2
        pnl_usd = pnl_usd_gross - total_commission

        # R-multiple: PnL relative to initial risk
        risk_amount = position.position_size_usd * abs(
            position.entry_price - position.stop_loss
        ) / position.entry_price
        r_multiple = pnl_usd / risk_amount if risk_amount > 0 else 0.0

        trade = TradeRecord(
            id=position.id,
            symbol=position.symbol,
            direction=position.direction,
            strategy=position.strategy,
            entry_price=position.entry_price,
            entry_time=position.entry_time,
            entry_bar=position.entry_bar,
            exit_price=exit_price,
            exit_time=timestamp,
            exit_bar=bar_index,
            exit_reason=reason,
            stop_loss=position.stop_loss,
            take_profit_levels=position.take_profit_levels,
            position_size_usd=position.position_size_usd,
            leverage=position.leverage,
            confidence=position.confidence,
            pnl_usd=round(pnl_usd, 4),
            pnl_pct=round(pnl_pct * 100, 4),
            commission_paid=round(total_commission, 4),
            duration_bars=bar_index - position.entry_bar,
            r_multiple=round(r_multiple, 4),
        )

        self.capital = round(self.capital + pnl_usd, 4)
        self.closed_trades.append(trade)
        self.open_positions = [
            p for p in self.open_positions if p.id != position.id
        ]
        return trade

    def record_equity(self, current_prices: dict[str, float] | None = None) -> None:
        """Record current equity (capital + unrealized PnL)."""
        unrealized = 0.0
        if current_prices:
            for pos in self.open_positions:
                price = current_prices.get(pos.symbol, pos.entry_price)
                if pos.direction == SignalDirection.LONG:
                    unrealized += pos.position_size_usd * (
                        (price - pos.entry_price) / pos.entry_price
                    )
                else:
                    unrealized += pos.position_size_usd * (
                        (pos.entry_price - price) / pos.entry_price
                    )
        self.equity_curve.append(round(self.capital + unrealized, 4))
