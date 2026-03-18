"""Layer 5 — Backtest Engine.

Usage::

    from strategy.layer5_backtest import BacktestEngine, BacktestConfig
    from strategy.layer3_strategies.strategy_b_squeeze import BBSqueezeStrategy

    config = BacktestConfig(symbol="BTC/USDT", timeframe="1h", initial_capital=10000)
    engine = BacktestEngine(strategy=BBSqueezeStrategy(), config=config)
    result = engine.run(df)

    print(f"Win rate: {result.win_rate:.1%}")
    print(f"Max DD: {result.max_drawdown:.1%}")
    print(f"Sharpe: {result.sharpe_ratio:.2f}")
"""

from .engine import BacktestEngine
from .metrics import compute_metrics
from .models import BacktestConfig, WalkForwardConfig
from .walk_forward import WalkForwardRunner

__all__ = [
    "BacktestEngine",
    "BacktestConfig",
    "WalkForwardConfig",
    "WalkForwardRunner",
    "compute_metrics",
]
