from __future__ import annotations

import pandas as pd
import yfinance as yf


class TWStockFetcher:
    """Fetches Taiwan stock market data.

    Uses yfinance with the .TW suffix as a temporary solution.
    TODO: Integrate with Fugle API or other Taiwan-specific data sources
    for better coverage of TWSE/TPEx listed stocks.
    """

    def _normalize_symbol(self, symbol: str) -> str:
        """Ensure symbol has .TW suffix for yfinance."""
        symbol = symbol.strip().upper()
        if symbol.endswith(".TW") or symbol.endswith(".TWO"):
            return symbol
        # Assume TWSE main board by default
        return f"{symbol}.TW"

    def fetch_ohlcv(self, symbol: str, interval: str = "1d", period: str = "6mo") -> pd.DataFrame:
        """Fetch OHLCV data for a Taiwan stock.

        Args:
            symbol: Taiwan stock code (e.g. "2330", "2330.TW").
            interval: Candlestick interval.
            period: Lookback period.

        Returns:
            DataFrame with OHLCV columns and DatetimeIndex.
        """
        yf_symbol = self._normalize_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            return pd.DataFrame()

        df.columns = [c.title() for c in df.columns]
        expected = ["Open", "High", "Low", "Close", "Volume"]
        available = [c for c in expected if c in df.columns]
        return df[available]

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch the latest quote for a Taiwan stock.

        Args:
            symbol: Taiwan stock code.

        Returns:
            Dictionary with price, change, change_percent, volume.
        """
        yf_symbol = self._normalize_symbol(symbol)
        ticker = yf.Ticker(yf_symbol)
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
            "symbol": yf_symbol,
            "price": last_price,
            "change": round(change, 4),
            "change_percent": round(change_pct, 2),
            "volume": int(getattr(info, "last_volume", 0) or 0),
        }
