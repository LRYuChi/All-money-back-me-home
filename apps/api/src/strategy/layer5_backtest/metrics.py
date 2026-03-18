"""Layer 5 — Performance metrics calculator."""

from __future__ import annotations

import math

from ..enums import StrategyName
from ..models import BacktestResult
from .models import BacktestConfig, TradeRecord


# Annualization factors: bars per year for common timeframes
_BARS_PER_YEAR = {
    "1h": 8760,
    "4h": 2190,
    "1d": 365,
    "1wk": 52,
    "1mo": 12,
}


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: list[float],
    config: BacktestConfig,
    strategy_name: StrategyName = StrategyName.BB_SQUEEZE,
) -> BacktestResult:
    """Compute backtest performance metrics from trade records and equity curve.

    Returns a populated :class:`BacktestResult`.
    """
    total_trades = len(trades)

    if total_trades == 0:
        return BacktestResult(
            strategy=strategy_name,
            symbol=config.symbol,
            period=config.timeframe,
            total_trades=0,
            equity_curve=equity_curve,
        )

    # Win rate
    wins = [t for t in trades if t.pnl_usd > 0]
    losses = [t for t in trades if t.pnl_usd <= 0]
    win_rate = len(wins) / total_trades

    # Profit factor
    gross_profit = sum(t.pnl_usd for t in wins)
    gross_loss = abs(sum(t.pnl_usd for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Total return
    final_equity = equity_curve[-1] if equity_curve else config.initial_capital
    total_return = (final_equity - config.initial_capital) / config.initial_capital

    # Max drawdown from equity curve
    max_drawdown = _compute_max_drawdown(equity_curve)

    # Sharpe ratio (annualized)
    sharpe_ratio = _compute_sharpe(equity_curve, config.timeframe)

    # Calmar ratio
    annualized_return = _annualize_return(total_return, len(equity_curve), config.timeframe)
    calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else float("inf")

    # Avg trade duration
    avg_duration_bars = sum(t.duration_bars for t in trades) / total_trades
    avg_r_multiple = sum(t.r_multiple for t in trades) / total_trades

    # Serialize trades for BacktestResult
    trade_dicts = [t.model_dump(mode="json") for t in trades]

    return BacktestResult(
        strategy=strategy_name,
        symbol=config.symbol,
        period=config.timeframe,
        total_trades=total_trades,
        win_rate=round(win_rate, 4),
        profit_factor=round(min(profit_factor, 999.0), 4),
        sharpe_ratio=round(sharpe_ratio, 4),
        max_drawdown=round(max_drawdown, 4),
        total_return=round(total_return, 4),
        equity_curve=equity_curve,
        trades=trade_dicts,
        avg_trade_duration_bars=round(avg_duration_bars, 2),
        calmar_ratio=round(min(calmar_ratio, 999.0), 4),
        avg_r_multiple=round(avg_r_multiple, 4),
    )


def _compute_max_drawdown(equity_curve: list[float]) -> float:
    """Peak-to-trough maximum drawdown as a fraction (0.0 to 1.0)."""
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_dd = 0.0

    for equity in equity_curve:
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    return max_dd


def _compute_sharpe(
    equity_curve: list[float],
    timeframe: str,
    risk_free_rate: float = 0.0,
) -> float:
    """Annualized Sharpe ratio from equity curve."""
    if len(equity_curve) < 2:
        return 0.0

    # Compute bar-to-bar returns
    returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
        if equity_curve[i - 1] > 0
    ]

    if not returns:
        return 0.0

    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
    std_return = math.sqrt(variance)

    if std_return == 0:
        return 0.0

    bars_per_year = _BARS_PER_YEAR.get(timeframe, 8760)
    annualization = math.sqrt(bars_per_year)

    return (mean_return - risk_free_rate) / std_return * annualization


def _annualize_return(
    total_return: float,
    n_bars: int,
    timeframe: str,
) -> float:
    """Convert total return to annualized return."""
    if n_bars <= 0:
        return 0.0

    bars_per_year = _BARS_PER_YEAR.get(timeframe, 8760)
    years = n_bars / bars_per_year

    if years <= 0:
        return 0.0

    if total_return <= -1.0:
        return -1.0

    return (1 + total_return) ** (1 / years) - 1
