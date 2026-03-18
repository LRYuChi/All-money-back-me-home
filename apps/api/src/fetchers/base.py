from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd


@runtime_checkable
class DataFetcher(Protocol):
    """Protocol for market data fetchers.

    All fetchers must implement these methods to provide a unified
    interface for fetching OHLCV data and live quotes across markets.
    """

    def fetch_ohlcv(self, symbol: str, interval: str = "1d", period: str = "6mo") -> pd.DataFrame:
        """Fetch OHLCV data for a symbol.

        Args:
            symbol: The ticker/symbol string.
            interval: Candlestick interval (e.g. "1d", "1h", "5m").
            period: Lookback period (e.g. "6mo", "1y").

        Returns:
            DataFrame with columns: Open, High, Low, Close, Volume
            and a DatetimeIndex.
        """
        ...

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch the latest quote/snapshot for a symbol.

        Args:
            symbol: The ticker/symbol string.

        Returns:
            Dictionary with at least: price, change, change_percent, volume.
        """
        ...
