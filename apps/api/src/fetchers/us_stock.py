from __future__ import annotations

import pandas as pd
import yfinance as yf


class USStockFetcher:
    """Fetches US stock market data using yfinance."""

    def fetch_ohlcv(self, symbol: str, interval: str = "1d", period: str = "6mo") -> pd.DataFrame:
        """Fetch OHLCV data for a US stock symbol.

        Args:
            symbol: US stock ticker (e.g. "AAPL", "MSFT").
            interval: Candlestick interval.
            period: Lookback period.

        Returns:
            DataFrame with OHLCV columns and DatetimeIndex.
        """
        ticker = yf.Ticker(symbol.upper())
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        # Normalize column names to ensure consistency
        df.columns = [c.title() for c in df.columns]

        # Keep only OHLCV columns
        expected = ["Open", "High", "Low", "Close", "Volume"]
        available = [c for c in expected if c in df.columns]
        return df[available]

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a US stock.

        Args:
            symbol: US stock ticker.

        Returns:
            Dictionary with price, change, change_percent, volume.
        """
        ticker = yf.Ticker(symbol.upper())
        info = ticker.fast_info

        try:
            last_price = float(info.last_price)
            prev_close = float(info.previous_close)
            change = last_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
        except (AttributeError, TypeError):
            last_price = 0.0
            change = 0.0
            change_pct = 0.0

        return {
            "symbol": symbol.upper(),
            "price": last_price,
            "change": round(change, 4),
            "change_percent": round(change_pct, 2),
            "volume": int(getattr(info, "last_volume", 0) or 0),
        }
