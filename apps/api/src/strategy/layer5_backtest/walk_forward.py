"""Layer 5 — Walk-Forward Validation runner."""

from __future__ import annotations

from typing import Callable

import pandas as pd

from ..models import BacktestResult
from .engine import BacktestEngine
from .models import BacktestConfig, WalkForwardConfig


class WalkForwardFold:
    """Results from a single walk-forward fold."""

    def __init__(
        self,
        fold_index: int,
        train_start: int,
        train_end: int,
        test_start: int,
        test_end: int,
    ) -> None:
        self.fold_index = fold_index
        self.train_start = train_start
        self.train_end = train_end
        self.test_start = test_start
        self.test_end = test_end
        self.train_result: BacktestResult | None = None
        self.test_result: BacktestResult | None = None

    def to_dict(self) -> dict:
        return {
            "fold": self.fold_index,
            "train_range": [self.train_start, self.train_end],
            "test_range": [self.test_start, self.test_end],
            "train": self.train_result.model_dump(mode="json") if self.train_result else None,
            "test": self.test_result.model_dump(mode="json") if self.test_result else None,
        }


class WalkForwardRunner:
    """Anchored walk-forward validation.

    Splits data into expanding train windows and fixed-size test windows.
    Runs a fresh backtest on each fold to evaluate out-of-sample performance.

    Usage::

        runner = WalkForwardRunner(
            strategy_factory=lambda: BBSqueezeStrategy(),
            backtest_config=config,
            wf_config=WalkForwardConfig(n_splits=5),
        )
        folds = runner.run(df)
    """

    def __init__(
        self,
        strategy_factory: Callable,
        backtest_config: BacktestConfig,
        wf_config: WalkForwardConfig | None = None,
    ) -> None:
        self._strategy_factory = strategy_factory
        self._bt_config = backtest_config
        self._wf_config = wf_config or WalkForwardConfig()

    def run(self, df: pd.DataFrame) -> list[WalkForwardFold]:
        """Run walk-forward validation over the full dataset."""
        folds = self._generate_folds(len(df))
        results: list[WalkForwardFold] = []

        for fold in folds:
            train_df = df.iloc[fold.train_start : fold.train_end]
            test_df = df.iloc[fold.test_start : fold.test_end]

            # Train fold
            engine = BacktestEngine(
                strategy=self._strategy_factory(),
                config=self._bt_config,
            )
            fold.train_result = engine.run(train_df)

            # Test fold (out-of-sample)
            engine = BacktestEngine(
                strategy=self._strategy_factory(),
                config=self._bt_config,
            )
            fold.test_result = engine.run(test_df)

            results.append(fold)

        return results

    def _generate_folds(self, n_bars: int) -> list[WalkForwardFold]:
        """Generate anchored walk-forward fold boundaries.

        Train window always starts at 0 and grows. Test window
        is the next chunk after the train window.
        """
        wf = self._wf_config
        min_total = wf.min_train_bars + wf.min_test_bars

        if n_bars < min_total:
            raise ValueError(
                f"Not enough data ({n_bars} bars) for walk-forward. "
                f"Need at least {min_total}."
            )

        # Compute test window size
        usable_bars = n_bars - wf.min_train_bars
        test_size = max(wf.min_test_bars, usable_bars // wf.n_splits)

        folds: list[WalkForwardFold] = []
        for i in range(wf.n_splits):
            train_end = wf.min_train_bars + i * test_size
            test_start = train_end
            test_end = min(test_start + test_size, n_bars)

            if test_start >= n_bars:
                break
            if test_end - test_start < wf.min_test_bars:
                break

            folds.append(
                WalkForwardFold(
                    fold_index=i,
                    train_start=0,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

        return folds

    @staticmethod
    def summarize(folds: list[WalkForwardFold]) -> dict:
        """Summarize walk-forward results across all folds."""
        test_results = [f.test_result for f in folds if f.test_result]
        if not test_results:
            return {"error": "No test results"}

        avg_win_rate = sum(r.win_rate for r in test_results) / len(test_results)
        avg_sharpe = sum(r.sharpe_ratio for r in test_results) / len(test_results)
        avg_max_dd = sum(r.max_drawdown for r in test_results) / len(test_results)
        avg_return = sum(r.total_return for r in test_results) / len(test_results)
        total_trades = sum(r.total_trades for r in test_results)

        return {
            "n_folds": len(test_results),
            "avg_win_rate": round(avg_win_rate, 4),
            "avg_sharpe": round(avg_sharpe, 4),
            "avg_max_drawdown": round(avg_max_dd, 4),
            "avg_return": round(avg_return, 4),
            "total_trades": total_trades,
            "folds": [f.to_dict() for f in folds],
        }
