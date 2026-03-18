"""Layer 5 — BacktestEngine: candle-by-candle backtest orchestrator."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from ..enums import SignalDirection, StrategyName
from ..models import BacktestResult, StrategySignal
from ..layer1_market_structure.swing_detector import (
    detect_swing_highs,
    detect_swing_lows,
)
from ..layer1_market_structure.structure_analyzer import classify_market_state
from ..layer2_signal_engine.trend_indicators import evaluate_trend_signals
from ..layer2_signal_engine.volatility_indicators import evaluate_volatility_signals
from .broker import SimulatedBroker
from .data_feed import DataFeed
from .metrics import compute_metrics
from .models import BacktestConfig


class BacktestEngine:
    """Event-driven backtest engine that replays historical OHLCV data
    through the Layer 1-4 pipeline.

    Usage::

        engine = BacktestEngine(strategy=BBSqueezeStrategy(), config=config)
        result = engine.run(df)
    """

    def __init__(
        self,
        strategy,
        config: BacktestConfig | None = None,
    ) -> None:
        self._strategy = strategy
        self._config = config or BacktestConfig()
        self._broker = SimulatedBroker(self._config)

    @property
    def broker(self) -> SimulatedBroker:
        return self._broker

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """Run a full backtest over the given OHLCV DataFrame.

        Parameters
        ----------
        df:
            DataFrame with Open, High, Low, Close, Volume columns
            and a DatetimeIndex.

        Returns
        -------
        BacktestResult
            Comprehensive performance statistics.
        """
        # Input validation
        if df is None or df.empty:
            return compute_metrics([], [], self._config)

        required_cols = {"Open", "High", "Low", "Close"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")

        if not isinstance(df.index, pd.DatetimeIndex):
            raise ValueError("DataFrame must have a DatetimeIndex")

        feed = DataFeed(df, warmup=self._config.warmup_bars)

        for bar_idx, window_df in feed:
            candle = df.iloc[bar_idx]
            timestamp = self._get_timestamp(df, bar_idx)
            current_price = float(candle["Close"])

            # 1. Check SL/TP on open positions (using High/Low)
            self._broker.check_positions(
                high=float(candle["High"]),
                low=float(candle["Low"]),
                close=current_price,
                bar_index=bar_idx,
                timestamp=timestamp,
            )

            # 2. Run Layer 1: Market Structure
            swing_highs = detect_swing_highs(window_df, lookback=5)
            swing_lows = detect_swing_lows(window_df, lookback=5)
            structure = classify_market_state(swing_highs, swing_lows)

            # 3. Run Layer 2: Indicators
            trend_signals = evaluate_trend_signals(window_df)
            vol_signals = evaluate_volatility_signals(window_df)

            # 4. Run Layer 3: Strategy
            signal = self._evaluate_strategy(
                window_df, structure, trend_signals, vol_signals
            )

            # 5. Open position if signal
            if signal and signal.direction != SignalDirection.NEUTRAL:
                self._broker.open_position(
                    symbol=self._config.symbol,
                    direction=signal.direction,
                    entry_price=signal.entry_price or current_price,
                    stop_loss=signal.stop_loss or self._default_sl(
                        current_price, signal.direction
                    ),
                    take_profit_levels=signal.take_profit_levels,
                    strategy=signal.strategy,
                    bar_index=bar_idx,
                    timestamp=timestamp,
                    confidence=signal.confidence,
                    reason=signal.reason_zh,
                )

            # 6. Record equity snapshot
            self._broker.record_equity(
                {self._config.symbol: current_price}
            )

        # Determine strategy name
        strategy_name = getattr(
            self._strategy, "name", StrategyName.BB_SQUEEZE
        )

        return compute_metrics(
            self._broker.closed_trades,
            self._broker.equity_curve,
            self._config,
            strategy_name=strategy_name,
        )

    def _evaluate_strategy(
        self,
        df: pd.DataFrame,
        structure,
        trend_signals,
        vol_signals,
    ) -> StrategySignal | None:
        """Dispatch to strategy, handling both BBSqueezeStrategy and BaseStrategy signatures."""
        try:
            # BBSqueezeStrategy.evaluate(df) — simple signature
            sig = self._strategy.evaluate(df)
            return sig
        except TypeError:
            pass

        try:
            # BaseStrategy.evaluate(df, structure, indicators)
            indicators = {
                "trend": trend_signals,
                "volatility": vol_signals,
            }
            return self._strategy.evaluate(df, structure, indicators)
        except Exception:
            return None

    @staticmethod
    def _get_timestamp(df: pd.DataFrame, idx: int) -> datetime:
        if isinstance(df.index, pd.DatetimeIndex):
            return df.index[idx].to_pydatetime()
        return datetime.now(tz=timezone.utc)

    @staticmethod
    def _default_sl(price: float, direction: SignalDirection) -> float:
        """Fallback stop-loss at 3% from entry."""
        if direction == SignalDirection.LONG:
            return price * 0.97
        return price * 1.03
