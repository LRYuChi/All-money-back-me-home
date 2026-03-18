"""Layer 3 — Abstract base class for all trading strategies."""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from strategy.enums import MarketState, StrategyName
from strategy.models import MarketStructureResult, StrategySignal


class BaseStrategy(ABC):
    """Every strategy must inherit from this class and implement ``evaluate``."""

    name: StrategyName
    allowed_states: list[MarketState]

    @abstractmethod
    def evaluate(
        self,
        df: pd.DataFrame,
        structure: MarketStructureResult,
        indicators: dict,
    ) -> StrategySignal | None:
        """Return a signal if conditions are met, ``None`` otherwise."""
        ...

    def is_active(self, state: MarketState) -> bool:
        """Check if this strategy should run in the given market state."""
        return state in self.allowed_states
