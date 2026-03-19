"""TWSE OpenAPI client — Taiwan Stock Exchange official Open Data.

Free, no API key required. JSON format.
Provides: daily OHLCV, PE/PB/yield, 112+ sector indices, monthly TAIEX stats.

Limitation: NO historical data — only current day / current month.
Use yfinance/twstock for historical data (RSI, MA200, etc.).

Endpoints:
    v1/exchangeReport/STOCK_DAY_ALL    — All listed stocks daily OHLCV
    v1/exchangeReport/BWIBBU_ALL       — PE / PB / Dividend Yield
    v1/exchangeReport/MI_INDEX         — 112 market indices
    v1/exchangeReport/FMTQIK           — Monthly TAIEX + volume stats
    v1/exchangeReport/STOCK_DAY_AVG_ALL — Closing + monthly avg price
    v1/opendata/t187ap03_L             — Company info (static)

Usage:
    from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
    client = TWSEOpenAPIClient()
    print(client.get_taiex())
    print(client.get_stock_fundamentals("2330"))
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

# Base URL
_BASE_URL = "https://openapi.twse.com.tw/v1"

# TTL cache storage: {endpoint: (data, timestamp)}
_cache: dict[str, tuple[list[dict], float]] = {}

# Cache TTL in seconds
_CACHE_TTL_MARKET = 3600      # 1 hour for daily market data (updates once per trading day)
_CACHE_TTL_STATIC = 86400     # 24 hours for company info

# Minimum interval between API calls (politeness)
_MIN_CALL_INTERVAL = 3.0
_last_call_ts: float = 0


def _fetch_endpoint(endpoint: str, cache_ttl: int = _CACHE_TTL_MARKET) -> list[dict]:
    """Fetch a TWSE OpenAPI endpoint with caching and rate limiting.

    Args:
        endpoint: API path after base URL (e.g. "exchangeReport/STOCK_DAY_ALL")
        cache_ttl: Cache time-to-live in seconds

    Returns:
        List of dicts from the JSON response, or empty list on error.
    """
    global _last_call_ts

    # Check cache
    if endpoint in _cache:
        data, ts = _cache[endpoint]
        if time.time() - ts < cache_ttl:
            return data

    # Rate limiting
    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    url = f"{_BASE_URL}/{endpoint}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        _last_call_ts = time.time()

        if isinstance(raw, list):
            _cache[endpoint] = (raw, time.time())
            return raw
        else:
            logger.warning("TWSE %s returned non-list: %s", endpoint, type(raw))
            return []
    except Exception as e:
        logger.warning("TWSE OpenAPI fetch failed [%s]: %s", endpoint, e)
        # Return stale cache if available
        if endpoint in _cache:
            return _cache[endpoint][0]
        return []


# =============================================
# Utility: ROC date & number parsing
# =============================================

def roc_to_date(roc_str: str) -> datetime | None:
    """Convert ROC (民國) date string to datetime.

    Formats handled:
        "1150317"  → 2026-03-17  (YYYMMDD, 7 digits)
        "115/03/17" → 2026-03-17 (YYY/MM/DD)

    Returns None if parsing fails.
    """
    if not roc_str or not isinstance(roc_str, str):
        return None

    roc_str = roc_str.strip()

    try:
        if "/" in roc_str:
            parts = roc_str.split("/")
            year = int(parts[0]) + 1911
            month = int(parts[1])
            day = int(parts[2])
        elif len(roc_str) == 7:
            year = int(roc_str[:3]) + 1911
            month = int(roc_str[3:5])
            day = int(roc_str[5:7])
        elif len(roc_str) == 8:
            # Gregorian YYYYMMDD (some endpoints use this)
            year = int(roc_str[:4])
            month = int(roc_str[4:6])
            day = int(roc_str[6:8])
        else:
            return None
        return datetime(year, month, day)
    except (ValueError, IndexError):
        return None


def clean_number(val: str) -> float | None:
    """Parse a TWSE numeric string to float.

    Handles: "1,234.56", "76.65", "", "--", "0", None
    Returns None if not parseable.
    """
    if val is None:
        return None
    if not isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    val = val.strip().replace(",", "")
    if not val or val in ("--", "-", "－", "N/A"):
        return None
    try:
        return float(val)
    except ValueError:
        return None


# =============================================
# High-level API
# =============================================

class TWSEOpenAPIClient:
    """Client for TWSE OpenAPI (台灣證券交易所 Open Data)."""

    # --- Raw data fetchers ---

    def fetch_stock_day_all(self) -> list[dict]:
        """All listed stocks daily OHLCV + volume/value/transactions."""
        return _fetch_endpoint("exchangeReport/STOCK_DAY_ALL")

    def fetch_bwibbu_all(self) -> list[dict]:
        """All listed stocks PE ratio / dividend yield / PB ratio."""
        return _fetch_endpoint("exchangeReport/BWIBBU_ALL")

    def fetch_mi_index(self) -> list[dict]:
        """112 market indices (TAIEX, sector indices, etc.)."""
        return _fetch_endpoint("exchangeReport/MI_INDEX")

    def fetch_fmtqik(self) -> list[dict]:
        """Current month daily TAIEX + total volume/value/transactions."""
        return _fetch_endpoint("exchangeReport/FMTQIK")

    def fetch_stock_day_avg_all(self) -> list[dict]:
        """All stocks closing price + monthly average price."""
        return _fetch_endpoint("exchangeReport/STOCK_DAY_AVG_ALL")

    def fetch_company_info(self) -> list[dict]:
        """Listed company basic info (industry, capital, etc.)."""
        return _fetch_endpoint("opendata/t187ap03_L", cache_ttl=_CACHE_TTL_STATIC)

    # --- Convenience methods ---

    def get_stock_quote(self, code: str) -> dict | None:
        """Get today's OHLCV for a single stock by code (e.g. '2330').

        Returns dict with: code, name, open, high, low, close, change,
                          volume, value, transactions, date
        Or None if not found.
        """
        code = code.strip()
        for row in self.fetch_stock_day_all():
            if row.get("Code") == code:
                return {
                    "code": code,
                    "name": row.get("Name", ""),
                    "open": clean_number(row.get("OpeningPrice")),
                    "high": clean_number(row.get("HighestPrice")),
                    "low": clean_number(row.get("LowestPrice")),
                    "close": clean_number(row.get("ClosingPrice")),
                    "change": clean_number(row.get("Change")),
                    "volume": clean_number(row.get("TradeVolume")),
                    "value": clean_number(row.get("TradeValue")),
                    "transactions": clean_number(row.get("Transaction")),
                    "date": roc_to_date(row.get("Date", "")),
                }
        return None

    def get_stock_fundamentals(self, code: str) -> dict | None:
        """Get PE/PB/Dividend Yield for a single stock.

        Returns dict with: code, name, pe_ratio, pb_ratio, dividend_yield, date
        Or None if not found.
        """
        code = code.strip()
        for row in self.fetch_bwibbu_all():
            if row.get("Code") == code:
                return {
                    "code": code,
                    "name": row.get("Name", ""),
                    "pe_ratio": clean_number(row.get("PEratio")),
                    "pb_ratio": clean_number(row.get("PBratio")),
                    "dividend_yield": clean_number(row.get("DividendYield")),
                    "date": roc_to_date(row.get("Date", "")),
                }
        return None

    def get_taiex(self) -> dict | None:
        """Get TAIEX (發行量加權股價指數) from MI_INDEX.

        Returns dict with: name, close, change, change_pct, date
        Or None if not found.
        """
        for row in self.fetch_mi_index():
            name = row.get("指數", "")
            if "發行量加權" in name:
                direction = row.get("漲跌", "+")
                points = clean_number(row.get("漲跌點數")) or 0
                if direction == "-":
                    points = -abs(points)
                return {
                    "name": name,
                    "close": clean_number(row.get("收盤指數")),
                    "change": points,
                    "change_pct": clean_number(row.get("漲跌百分比")),
                    "date": roc_to_date(row.get("日期", "")),
                }
        return None

    def get_sector_indices(self) -> list[dict]:
        """Get all sector indices from MI_INDEX.

        Returns list of dicts with: name, close, change, change_pct
        """
        results = []
        for row in self.fetch_mi_index():
            direction = row.get("漲跌", "+")
            points = clean_number(row.get("漲跌點數")) or 0
            if direction == "-":
                points = -abs(points)
            results.append({
                "name": row.get("指數", ""),
                "close": clean_number(row.get("收盤指數")),
                "change": points,
                "change_pct": clean_number(row.get("漲跌百分比")),
            })
        return results

    def get_watchlist_fundamentals(self, codes: list[str]) -> list[dict]:
        """Get fundamentals for multiple stocks at once (efficient: single API call).

        Args:
            codes: List of stock codes (e.g. ["2330", "2317", "2454"])

        Returns:
            List of fundamentals dicts (same as get_stock_fundamentals)
        """
        code_set = {c.strip() for c in codes}
        results = []
        for row in self.fetch_bwibbu_all():
            if row.get("Code") in code_set:
                results.append({
                    "code": row.get("Code"),
                    "name": row.get("Name", ""),
                    "pe_ratio": clean_number(row.get("PEratio")),
                    "pb_ratio": clean_number(row.get("PBratio")),
                    "dividend_yield": clean_number(row.get("DividendYield")),
                    "date": roc_to_date(row.get("Date", "")),
                })
        return results

    def get_monthly_taiex(self) -> list[dict]:
        """Get current month's daily TAIEX + volume from FMTQIK.

        Returns list of dicts with: date, taiex, change, volume, value, transactions
        """
        results = []
        for row in self.fetch_fmtqik():
            results.append({
                "date": roc_to_date(row.get("Date", "")),
                "taiex": clean_number(row.get("TAIEX")),
                "change": clean_number(row.get("Change")),
                "volume": clean_number(row.get("TradeVolume")),
                "value": clean_number(row.get("TradeValue")),
                "transactions": clean_number(row.get("Transaction")),
            })
        return results
