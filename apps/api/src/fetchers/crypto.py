from __future__ import annotations

from datetime import datetime, timezone

import ccxt
import pandas as pd


# Mapping from period strings to millisecond durations (approximate)
_PERIOD_MS: dict[str, int] = {
    "1mo": 30 * 24 * 60 * 60 * 1000,
    "3mo": 90 * 24 * 60 * 60 * 1000,
    "6mo": 180 * 24 * 60 * 60 * 1000,
    "1y": 365 * 24 * 60 * 60 * 1000,
    "2y": 730 * 24 * 60 * 60 * 1000,
}

# Mapping from yfinance-style intervals to ccxt timeframes
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
    "1wk": "1w",
    "1mo": "1M",
}


class CryptoFetcher:
    """Fetches cryptocurrency data using ccxt (Binance by default)."""

    def __init__(self, exchange_id: str = "binance"):
        self._exchange = getattr(ccxt, exchange_id)(
            {"enableRateLimit": True}
        )

    def _normalize_symbol(self, symbol: str) -> str:
        """Ensure symbol is in ccxt format (e.g. BTC/USDT)."""
        symbol = symbol.upper()
        if "/" in symbol:
            return symbol
        # Common suffixes
        for quote in ("USDT", "USD", "BTC", "ETH", "BUSD"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                base = symbol[: -len(quote)]
                return f"{base}/{quote}"
        # Default to USDT pair
        return f"{symbol}/USDT"

    def fetch_ohlcv(self, symbol: str, interval: str = "1d", period: str = "6mo") -> pd.DataFrame:
        """Fetch OHLCV data for a cryptocurrency pair.

        Args:
            symbol: Crypto pair (e.g. "BTC/USDT", "BTCUSDT", "ETH").
            interval: Candlestick interval.
            period: Lookback period.

        Returns:
            DataFrame with OHLCV columns and DatetimeIndex.
        """
        ccxt_symbol = self._normalize_symbol(symbol)
        timeframe = _INTERVAL_MAP.get(interval, interval)

        since_ms = _PERIOD_MS.get(period, _PERIOD_MS["6mo"])
        since = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - since_ms

        all_candles: list[list] = []
        limit = 1000

        while True:
            candles = self._exchange.fetch_ohlcv(
                ccxt_symbol, timeframe=timeframe, since=since, limit=limit
            )
            if not candles:
                break
            all_candles.extend(candles)
            if len(candles) < limit:
                break
            since = candles[-1][0] + 1  # next batch starts after last candle

        if not all_candles:
            return pd.DataFrame()

        df = pd.DataFrame(all_candles, columns=["Timestamp", "Open", "High", "Low", "Close", "Volume"])
        df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="ms", utc=True)
        df.set_index("Timestamp", inplace=True)
        df.sort_index(inplace=True)

        # Remove duplicates from pagination overlap
        df = df[~df.index.duplicated(keep="last")]

        return df

    def fetch_quote(self, symbol: str) -> dict:
        """Fetch the latest ticker/quote for a cryptocurrency pair.

        Args:
            symbol: Crypto pair.

        Returns:
            Dictionary with price, change, change_percent, volume.
        """
        ccxt_symbol = self._normalize_symbol(symbol)
        ticker = self._exchange.fetch_ticker(ccxt_symbol)

        return {
            "symbol": ccxt_symbol,
            "price": float(ticker.get("last", 0) or 0),
            "change": float(ticker.get("change", 0) or 0),
            "change_percent": float(ticker.get("percentage", 0) or 0),
            "volume": float(ticker.get("baseVolume", 0) or 0),
        }
