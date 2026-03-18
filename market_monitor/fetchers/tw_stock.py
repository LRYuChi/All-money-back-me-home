"""Taiwan stock data fetcher via twstock."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from market_monitor.fetchers.base import BaseFetcher

logger = logging.getLogger(__name__)


class TWStockFetcher(BaseFetcher):
    """Fetch Taiwan stock data via twstock library."""

    def fetch(
        self,
        symbol: str,
        start_date: str,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a Taiwan stock.

        Note: twstock fetches by year/month, so we iterate over the date range.
        For TWII index, falls back to yfinance.
        """
        try:
            # For Taiwan index, use yfinance
            if symbol.startswith("^"):
                import yfinance as yf

                ticker = yf.Ticker(symbol)
                df = ticker.history(start=start_date, end=end_date)
                df = self.normalize_columns(df)
                df = df[[c for c in df.columns if c in ["Open", "High", "Low", "Close", "Volume"]]]
                return df if self.validate(df) else pd.DataFrame()

            # For individual TW stocks, use twstock
            import twstock

            stock = twstock.Stock(symbol)
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(end_date, "%Y-%m-%d") if end_date else datetime.now()

            all_data = []
            current = start_dt
            while current <= end_dt:
                data = stock.fetch(current.year, current.month)
                for d in data:
                    all_data.append({
                        "date": d.date,
                        "Open": d.open,
                        "High": d.high,
                        "Low": d.low,
                        "Close": d.close,
                        "Volume": d.capacity,
                    })
                # Move to next month
                if current.month == 12:
                    current = current.replace(year=current.year + 1, month=1)
                else:
                    current = current.replace(month=current.month + 1)

            if not all_data:
                return pd.DataFrame()

            df = pd.DataFrame(all_data)
            df["date"] = pd.to_datetime(df["date"], utc=True)
            df = df.set_index("date").sort_index()
            df = df[(df.index >= start_date)]
            if end_date:
                df = df[(df.index <= end_date)]

            return df if self.validate(df) else pd.DataFrame()

        except Exception as e:
            logger.error("Failed to fetch TW stock %s: %s", symbol, e)
            return pd.DataFrame()
