from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

# Add project root so we can import market_monitor
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)


class TWStockFetcher:
    """Fetches Taiwan stock market data.

    Primary: yfinance (historical OHLCV for technical indicators).
    Supplementary: TWSE OpenAPI (official — daily quotes as fallback, fundamentals).
    """

    def _normalize_symbol(self, symbol: str) -> str:
        """Ensure symbol has .TW suffix for yfinance."""
        symbol = symbol.strip().upper()
        if symbol.endswith(".TW") or symbol.endswith(".TWO"):
            return symbol
        # Assume TWSE main board by default
        return f"{symbol}.TW"

    def _extract_code(self, symbol: str) -> str:
        """Extract numeric stock code from symbol (e.g. '2330.TW' -> '2330')."""
        return symbol.strip().upper().replace(".TW", "").replace(".TWO", "")

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

        Tries yfinance first, falls back to TWSE OpenAPI on failure.

        Returns:
            Dictionary with price, change, change_percent, volume.
        """
        # Try yfinance first
        yf_symbol = self._normalize_symbol(symbol)
        try:
            ticker = yf.Ticker(yf_symbol)
            info = ticker.fast_info
            last_price = float(info.last_price)
            prev_close = float(info.previous_close)
            change = last_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            return {
                "symbol": yf_symbol,
                "price": last_price,
                "change": round(change, 4),
                "change_percent": round(change_pct, 2),
                "volume": int(getattr(info, "last_volume", 0) or 0),
            }
        except Exception:
            pass

        # Fallback: TWSE OpenAPI
        try:
            from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
            code = self._extract_code(symbol)
            quote = TWSEOpenAPIClient().get_stock_quote(code)
            if quote and quote.get("close") is not None:
                return {
                    "symbol": yf_symbol,
                    "price": quote["close"],
                    "change": quote.get("change") or 0,
                    "change_percent": round(
                        (quote["change"] / (quote["close"] - quote["change"]) * 100)
                        if quote.get("change") and quote["close"] != quote["change"]
                        else 0, 2
                    ),
                    "volume": int(quote.get("volume") or 0),
                }
        except Exception as e:
            logger.warning("TWSE OpenAPI fallback failed for %s: %s", symbol, e)

        return {
            "symbol": yf_symbol,
            "price": 0.0,
            "change": 0.0,
            "change_percent": 0.0,
            "volume": 0,
        }

    def fetch_fundamentals(self, symbol: str) -> dict | None:
        """Fetch PE/PB/Dividend Yield from TWSE OpenAPI.

        Args:
            symbol: Taiwan stock code (e.g. "2330", "2330.TW").

        Returns:
            Dict with pe_ratio, pb_ratio, dividend_yield, or None if unavailable.
        """
        try:
            from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
            code = self._extract_code(symbol)
            return TWSEOpenAPIClient().get_stock_fundamentals(code)
        except Exception as e:
            logger.warning("TWSE fundamentals failed for %s: %s", symbol, e)
            return None
