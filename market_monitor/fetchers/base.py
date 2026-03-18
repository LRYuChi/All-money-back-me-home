"""Base data fetcher abstraction (inspired by ai-trader BaseFetcher)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class BaseFetcher(ABC):
    """Abstract base for all market data fetchers."""

    @abstractmethod
    def fetch(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a symbol.

        Returns:
            DataFrame with columns: date (index), open, high, low, close, volume
        """

    def validate(self, df: pd.DataFrame) -> bool:
        """Validate that DataFrame has required columns."""
        if df.empty:
            logger.warning("Empty DataFrame returned")
            return False
        missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            logger.warning("Missing columns: %s", missing)
            return False
        return True

    def normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names to Title Case (Open, High, Low, Close, Volume)."""
        df.columns = [c.strip().title() for c in df.columns]
        return df
