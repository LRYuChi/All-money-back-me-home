"""US stock & macro data fetcher via yfinance."""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import yfinance as yf

from market_monitor.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)


class USStockFetcher(BaseFetcher):
    """Fetch US stock, index, and macro data from Yahoo Finance."""

    def fetch(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a US stock/index/macro symbol."""
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(start=start_date, end=end_date)
            if df.empty:
                logger.warning("No data returned for %s", symbol)
                return pd.DataFrame()

            df = self.normalize_columns(df)
            # yfinance uses 'stock splits' and 'dividends' columns we don't need
            df = df[[c for c in df.columns if c in ["Open", "High", "Low", "Close", "Volume"]]]

            if self.validate(df):
                return df
            return pd.DataFrame()
        except Exception as e:
            logger.error("Failed to fetch %s: %s", symbol, e)
            return pd.DataFrame()

    def fetch_batch(
        self,
        symbols: list[str],
        start_date: str,
        end_date: Optional[str] = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch data for multiple symbols."""
        results = {}
        for symbol in symbols:
            df = self.fetch(symbol, start_date, end_date)
            if not df.empty:
                results[symbol] = df
        return results
