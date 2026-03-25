"""TWSE OpenAPI client — Taiwan Stock Exchange official Open Data.

Free, no API key required. JSON format.
Provides: daily OHLCV, PE/PB/yield, 112+ sector indices, monthly TAIEX stats,
          institutional investor (三大法人) buy/sell data.

Limitation: NO historical data — only current day / current month.
Use yfinance/twstock for historical data (RSI, MA200, etc.).

Endpoints (OpenAPI — openapi.twse.com.tw):
    v1/exchangeReport/STOCK_DAY_ALL    — All listed stocks daily OHLCV
    v1/exchangeReport/BWIBBU_ALL       — PE / PB / Dividend Yield
    v1/exchangeReport/MI_INDEX         — 112 market indices
    v1/exchangeReport/FMTQIK           — Monthly TAIEX + volume stats
    v1/exchangeReport/STOCK_DAY_AVG_ALL — Closing + monthly avg price
    v1/opendata/t187ap03_L             — Company info (static)

Endpoints (TWSE website — www.twse.com.tw):
    /rwd/zh/fund/T86                   — 三大法人買賣超個股明細 (listed)

Endpoints (TPEx — www.tpex.org.tw):
    /web/stock/3insti/daily_trade/3itrade_hedge_result.php — 三大法人買賣超 (OTC)

Usage:
    from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
    client = TWSEOpenAPIClient()
    print(client.get_taiex())
    print(client.get_stock_fundamentals("2330"))
    print(client.get_institutional_daily())
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import datetime, date as date_type

logger = logging.getLogger(__name__)

# Base URLs
_BASE_URL = "https://openapi.twse.com.tw/v1"
_TWSE_URL = "https://www.twse.com.tw"
_TPEX_URL = "https://www.tpex.org.tw"

# TTL cache storage: {endpoint: (data, timestamp)}
_cache: dict[str, tuple[list[dict] | dict, float]] = {}

# Cache TTL in seconds
_CACHE_TTL_MARKET = 3600      # 1 hour for daily market data (updates once per trading day)
_CACHE_TTL_STATIC = 86400     # 24 hours for company info
_CACHE_TTL_INST = 7200        # 2 hours for institutional data (updates ~16:30 after close)

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
# Institutional investor (三大法人) fetchers
# =============================================

def _today_yyyymmdd() -> str:
    """Return today's date as YYYYMMDD string."""
    return date_type.today().strftime("%Y%m%d")


def _today_roc_slash() -> str:
    """Return today's date in ROC format YYY/MM/DD (e.g. '115/03/25')."""
    today = date_type.today()
    roc_year = today.year - 1911
    return f"{roc_year}/{today.month:02d}/{today.day:02d}"


def _fetch_bfi82u(trade_date: str | None = None) -> dict:
    """Fetch TWSE BFI82U 三大法人買賣金額統計表 (market-level, in NTD).

    Args:
        trade_date: YYYYMMDD format. Defaults to today.

    Returns:
        Raw JSON response dict with 'stat', 'data', 'fields' keys.
        data rows: [單位名稱, 買進金額, 賣出金額, 買賣差額]
        Returns empty dict on error.
    """
    global _last_call_ts
    trade_date = trade_date or _today_yyyymmdd()
    cache_key = f"bfi82u_{trade_date}"

    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL_INST:
            return data  # type: ignore[return-value]

    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    url = (
        f"{_TWSE_URL}/rwd/zh/fund/BFI82U"
        f"?dayDate={trade_date}&type=day&response=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        _last_call_ts = time.time()

        if isinstance(raw, dict) and raw.get("stat") == "OK" and raw.get("data"):
            _cache[cache_key] = (raw, time.time())
            return raw
        else:
            logger.warning("TWSE BFI82U stat=%s", raw.get("stat") if isinstance(raw, dict) else "?")
            return {}
    except Exception as e:
        logger.warning("TWSE BFI82U fetch failed: %s", e)
        if cache_key in _cache:
            return _cache[cache_key][0]  # type: ignore[return-value]
        return {}


def _fetch_twse_institutional(trade_date: str | None = None) -> dict:
    """Fetch TWSE T86 三大法人買賣超個股明細 (listed stocks).

    Args:
        trade_date: YYYYMMDD format. Defaults to today.

    Returns:
        Raw JSON response dict with 'stat', 'data', 'fields' keys.
        Returns empty dict on error.
    """
    global _last_call_ts
    trade_date = trade_date or _today_yyyymmdd()
    cache_key = f"twse_t86_{trade_date}"

    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL_INST:
            return data  # type: ignore[return-value]

    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    url = (
        f"{_TWSE_URL}/rwd/zh/fund/T86"
        f"?date={trade_date}&selectType=ALLBUT0999&response=json"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        _last_call_ts = time.time()

        if isinstance(raw, dict) and raw.get("stat") == "OK" and raw.get("data"):
            _cache[cache_key] = (raw, time.time())
            return raw
        else:
            logger.warning("TWSE T86 returned stat=%s", raw.get("stat") if isinstance(raw, dict) else "?")
            return {}
    except Exception as e:
        logger.warning("TWSE T86 fetch failed: %s", e)
        if cache_key in _cache:
            return _cache[cache_key][0]  # type: ignore[return-value]
        return {}


def _fetch_tpex_institutional(trade_date: str | None = None) -> dict:
    """Fetch TPEx 三大法人買賣超 (OTC stocks).

    Args:
        trade_date: ROC format YYY/MM/DD. Defaults to today.

    Returns:
        Raw JSON response dict, or empty dict on error.
    """
    global _last_call_ts
    trade_date = trade_date or _today_roc_slash()
    cache_key = f"tpex_3insti_{trade_date}"

    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if time.time() - ts < _CACHE_TTL_INST:
            return data  # type: ignore[return-value]

    elapsed = time.time() - _last_call_ts
    if elapsed < _MIN_CALL_INTERVAL:
        time.sleep(_MIN_CALL_INTERVAL - elapsed)

    url = (
        f"{_TPEX_URL}/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
        f"?l=zh-tw&o=json&se=EW&t=D&d={trade_date}&s=0,asc"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
        _last_call_ts = time.time()

        if isinstance(raw, dict) and raw.get("aaData"):
            _cache[cache_key] = (raw, time.time())
            return raw
        else:
            logger.warning("TPEx 3insti returned no aaData")
            return {}
    except Exception as e:
        logger.warning("TPEx 3insti fetch failed: %s", e)
        if cache_key in _cache:
            return _cache[cache_key][0]  # type: ignore[return-value]
        return {}


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

    def fetch_margin_all(self) -> list[dict]:
        """All listed stocks margin trading (融資融券) data."""
        return _fetch_endpoint("exchangeReport/MI_MARGN")

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

    # --- Institutional investor (三大法人) methods ---

    def get_institutional_daily(self, trade_date: str | None = None) -> dict:
        """Get 三大法人買賣超 — market-level amounts (BFI82U) + per-stock shares (T86).

        Returns dict with:
            date: str (YYYYMMDD)
            -- Market-level in 億元 (from BFI82U) --
            foreign: {buy, sell, net}      — 外資 (億元)
            trust: {buy, sell, net}        — 投信 (億元)
            dealer: {buy, sell, net}       — 自營商 (億元)
            total: {buy, sell, net}        — 合計 (億元)
            -- Per-stock in shares (from T86) --
            stocks: list[dict]             — 個股明細 (foreign_net/trust_net/dealer_net/total_net in shares)
        """
        trade_date = trade_date or _today_yyyymmdd()
        result: dict = {"date": trade_date}

        # ---- BFI82U: market-level amounts (NTD) ----
        bfi = _fetch_bfi82u(trade_date)
        if bfi and bfi.get("data"):
            # BFI82U rows:
            #   [0] 自營商(自行買賣): buy, sell, net
            #   [1] 自營商(避險):     buy, sell, net
            #   [2] 投信:             buy, sell, net
            #   [3] 外資及陸資:       buy, sell, net
            #   [4] 外資自營商:       buy, sell, net
            #   [5] 合計:             buy, sell, net
            # Values are NTD strings with commas. Convert to 億元.
            YI = 1e8  # 1億 = 100,000,000

            def _parse_row(row: list) -> dict:
                return {
                    "buy": round((clean_number(row[1]) or 0) / YI, 2),
                    "sell": round((clean_number(row[2]) or 0) / YI, 2),
                    "net": round((clean_number(row[3]) or 0) / YI, 2),
                }

            rows = bfi["data"]
            if len(rows) >= 6:
                dealer_self = _parse_row(rows[0])
                dealer_hedge = _parse_row(rows[1])
                result["dealer"] = {
                    "buy": round(dealer_self["buy"] + dealer_hedge["buy"], 2),
                    "sell": round(dealer_self["sell"] + dealer_hedge["sell"], 2),
                    "net": round(dealer_self["net"] + dealer_hedge["net"], 2),
                }
                result["trust"] = _parse_row(rows[2])
                foreign_main = _parse_row(rows[3])
                foreign_prop = _parse_row(rows[4])
                result["foreign"] = {
                    "buy": round(foreign_main["buy"] + foreign_prop["buy"], 2),
                    "sell": round(foreign_main["sell"] + foreign_prop["sell"], 2),
                    "net": round(foreign_main["net"] + foreign_prop["net"], 2),
                }
                result["total"] = _parse_row(rows[5])

        # ---- T86: per-stock shares ----
        raw = _fetch_twse_institutional(trade_date)
        if raw and raw.get("data"):
            stocks = []
            for row in raw["data"]:
                if not isinstance(row, list) or len(row) < 19:
                    continue
                code = row[0].strip().replace("=", "").replace('"', '')
                name = row[1].strip()
                stocks.append({
                    "code": code,
                    "name": name,
                    "foreign_net": clean_number(row[4]) or 0,    # shares
                    "trust_net": clean_number(row[10]) or 0,     # shares
                    "dealer_net": clean_number(row[11]) or 0,    # shares
                    "total_net": clean_number(row[18]) or 0,     # shares
                })
            result["stocks"] = stocks

        return result if len(result) > 1 else {}

    def get_institutional_for_stocks(
        self, codes: list[str], trade_date: str | None = None
    ) -> list[dict]:
        """Get 三大法人 buy/sell data for specific stocks.

        Args:
            codes: Stock codes (e.g. ["2330", "2317"])
            trade_date: YYYYMMDD format. Defaults to today.

        Returns:
            List of dicts with: code, name, foreign_net, trust_net,
                               dealer_net, total_net (all in shares)
        """
        daily = self.get_institutional_daily(trade_date)
        if not daily or not daily.get("stocks"):
            return []

        code_set = {c.strip() for c in codes}
        return [s for s in daily["stocks"] if s["code"] in code_set]

    def get_tpex_institutional_daily(self, trade_date: str | None = None) -> dict:
        """Get 三大法人買賣超 for OTC stocks (TPEx/櫃買).

        Returns dict with:
            date: str
            stocks: list[dict] — code, name, foreign_net, trust_net, dealer_net, total_net
        """
        raw = _fetch_tpex_institutional(trade_date)
        if not raw or not raw.get("aaData"):
            return {}

        # TPEx aaData columns:
        #   0: 代號, 1: 名稱, 2: 外資及陸資(不含外資自營)-買進, 3: 賣出, 4: 買賣超,
        #   5: 外資自營-買, 6: 賣, 7: 買賣超,
        #   8: 投信-買, 9: 賣, 10: 買賣超,
        #   11: 自營-買賣超, 12: 自行買賣-買, 13: 賣, 14: 買賣超,
        #   15: 避險-買, 16: 賣, 17: 買賣超, 18: 三大法人合計
        stocks = []
        for row in raw["aaData"]:
            if not isinstance(row, list) or len(row) < 19:
                continue
            code = str(row[0]).strip()
            name = str(row[1]).strip()
            foreign_net = clean_number(str(row[4])) or 0
            trust_net = clean_number(str(row[10])) or 0
            dealer_net = clean_number(str(row[11])) or 0
            total_net = clean_number(str(row[18])) or 0
            stocks.append({
                "code": code,
                "name": name,
                "foreign_net": foreign_net,
                "trust_net": trust_net,
                "dealer_net": dealer_net,
                "total_net": total_net,
            })

        return {
            "date": trade_date or _today_roc_slash(),
            "stocks": stocks,
        }
