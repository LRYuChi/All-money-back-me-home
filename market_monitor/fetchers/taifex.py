"""TAIFEX (台灣期貨交易所) data fetcher.

Fetches:
- Put/Call Ratio (CSV endpoint, direct GET)
- 三大法人期貨淨部位 (POST + HTML parsing)
- 選擇權 OI 分布

Data source: https://www.taifex.com.tw
All public data, no API key required.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_TW_TZ = timezone(timedelta(hours=8))
_cache: dict[str, tuple[object, float]] = {}
_CACHE_TTL = 3600  # 1 hour


def _cached(key: str, ttl: int = _CACHE_TTL):
    """Simple TTL cache decorator."""
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set_cache(key: str, data):
    _cache[key] = (data, time.time())


def fetch_pc_ratio() -> dict:
    """Fetch Put/Call Ratio from TAIFEX (CSV endpoint).

    Returns latest day's PC ratio for volume and OI.
    """
    cached = _cached("pc_ratio")
    if cached:
        return cached

    try:
        url = "https://www.taifex.com.tw/cht/3/pcRatioDown"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("big5", errors="replace")

        lines = raw.strip().split("\n")
        if len(lines) < 2:
            return {"error": "No PC ratio data"}

        # Header: 日期,賣權成交量,買權成交量,買賣權成交量比率%,賣權未平倉量,買權未平倉量,買賣權未平倉量比率%
        latest = lines[1].strip().rstrip(",").split(",")
        if len(latest) < 7:
            return {"error": "Incomplete PC ratio data"}

        result = {
            "date": latest[0],
            "put_volume": int(latest[1]),
            "call_volume": int(latest[2]),
            "volume_pc_ratio": float(latest[3]),  # > 100 = more puts (bearish sentiment)
            "put_oi": int(latest[4]),
            "call_oi": int(latest[5]),
            "oi_pc_ratio": float(latest[6]),  # > 100 = more put OI
        }

        # Also get 5-day trend
        if len(lines) >= 6:
            ratios_5d = []
            for line in lines[1:6]:
                parts = line.strip().rstrip(",").split(",")
                if len(parts) >= 7:
                    ratios_5d.append(float(parts[6]))
            result["oi_pc_5d_avg"] = round(sum(ratios_5d) / len(ratios_5d), 2) if ratios_5d else 0

        _set_cache("pc_ratio", result)
        return result

    except Exception as e:
        logger.warning("TAIFEX PC ratio fetch failed: %s", e)
        return {"error": str(e)}


def fetch_futures_institutional() -> dict:
    """Fetch 三大法人期貨淨部位 from TAIFEX.

    Uses POST to query institutional futures trading data.
    Returns net contracts for foreign investors, investment trust, dealers.
    """
    cached = _cached("futures_inst")
    if cached:
        return cached

    # Try recent trading dates
    today = datetime.now(_TW_TZ)
    for days_back in range(5):
        query_date = today - timedelta(days=days_back)
        if query_date.weekday() >= 5:  # Skip weekends
            continue

        try:
            url = "https://www.taifex.com.tw/cht/3/futContractsDate"
            params = urllib.parse.urlencode({
                "queryType": "1",
                "goession": "",
                "doQuery": "1",
                "dateaddcnt": "",
                "queryDate": query_date.strftime("%Y/%m/%d"),
                "commodityId": "TXF",
            }).encode()

            req = urllib.request.Request(url, data=params, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            if "臺股期貨" not in html and "臺指期貨" not in html:
                continue

            # Parse HTML table — extract numbers from td tags
            # Pattern: find rows containing 外資, 投信, 自營商
            result = _parse_futures_html(html, query_date.strftime("%Y/%m/%d"))
            if result:
                _set_cache("futures_inst", result)
                return result

        except Exception as e:
            logger.debug("TAIFEX futures date %s failed: %s", query_date.strftime("%Y/%m/%d"), e)
            continue

    return {"error": "No recent futures data available"}


def _parse_futures_html(html: str, date: str) -> dict | None:
    """Parse institutional futures positions from TAIFEX HTML."""
    result = {"date": date}

    # Extract all numbers from table cells
    # Look for patterns: 多方(long) contracts, 空方(short) contracts, 淨額(net)
    # TAIFEX format: each row has 多方口數, 多方契約金額, 空方口數, 空方契約金額, 多空淨額口數, 多空淨額金額

    # Find table rows with institutional names
    patterns = {
        "foreign": r"外資",
        "trust": r"投信",
        "dealer": r"自營商",
    }

    for key, pattern in patterns.items():
        match = re.search(
            pattern + r'.*?(?:[\d,]+\s*){4,}',
            html, re.DOTALL
        )
        if match:
            # Extract numbers from this section
            numbers = re.findall(r'[\d,]+', match.group())
            numbers = [int(n.replace(",", "")) for n in numbers if n.replace(",", "").isdigit()]
            if len(numbers) >= 6:
                # Typical order: long_contracts, long_value, short_contracts, short_value, net_contracts, net_value
                result[f"{key}_long"] = numbers[0]
                result[f"{key}_short"] = numbers[2]
                result[f"{key}_net"] = numbers[4] if len(numbers) > 4 else numbers[0] - numbers[2]

    if len(result) > 1:  # Has at least one institutional entry
        return result
    return None


def fetch_options_max_oi() -> dict:
    """Fetch options OI distribution by strike price.

    Downloads TXO daily data CSV from TAIFEX, parses Call/Put OI per strike.
    Returns max Call OI strike (resistance) and max Put OI strike (support).
    """
    cached = _cached("options_max_oi")
    if cached:
        return cached

    today = datetime.now(_TW_TZ)

    for days_back in range(7):
        query_date = today - timedelta(days=days_back)
        if query_date.weekday() >= 5:
            continue
        date_str = query_date.strftime("%Y/%m/%d")

        try:
            url = "https://www.taifex.com.tw/cht/3/optDataDown"
            params = urllib.parse.urlencode({
                "down_type": "1",
                "queryStartDate": date_str,
                "queryEndDate": date_str,
                "commodity_id": "TXO",
            }).encode()

            req = urllib.request.Request(url, data=params, headers=_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()

            text = raw.decode("big5", errors="replace")
            lines = text.strip().split("\n")

            if len(lines) < 10:
                continue

            # Parse CSV: 交易日期,契約,到期月份,履約價,買賣權,...,未沖銷契約數,...
            call_oi = {}
            put_oi = {}

            for line in lines[1:]:
                parts = line.split(",")
                if len(parts) < 12:
                    continue

                strike_str = parts[3].strip()
                cp = parts[4].strip()
                oi_str = parts[11].strip()
                session = parts[17].strip() if len(parts) > 17 else ""

                # Only count regular session
                if "盤後" in session:
                    continue

                try:
                    strike = int(float(strike_str))
                    oi = int(oi_str)
                except (ValueError, TypeError):
                    continue

                if 10000 <= strike <= 50000 and oi > 0:
                    if "買" in cp:
                        call_oi[strike] = call_oi.get(strike, 0) + oi
                    elif "賣" in cp:
                        put_oi[strike] = put_oi.get(strike, 0) + oi

            if call_oi and put_oi:
                # Get current TAIEX price for OTM filtering
                current_price = 0
                try:
                    import yfinance as yf
                    taiex = yf.download("^TWII", period="1d", progress=False)
                    if taiex is not None and len(taiex) > 0:
                        current_price = int(float(taiex["Close"].iloc[-1]))
                except Exception:
                    # Estimate from strike midpoint
                    all_s = sorted(set(list(call_oi.keys()) + list(put_oi.keys())))
                    current_price = all_s[len(all_s) // 2] if all_s else 22000

                # OTM filter: Call above current = resistance, Put below current = support
                otm_call = {s: o for s, o in call_oi.items() if s > current_price}
                otm_put = {s: o for s, o in put_oi.items() if s < current_price}

                call_sorted = sorted(otm_call.items(), key=lambda x: x[1], reverse=True)
                put_sorted = sorted(otm_put.items(), key=lambda x: x[1], reverse=True)

                result = {
                    "date": date_str,
                    "current_price": current_price,
                    "max_call_strike": call_sorted[0][0] if call_sorted else 0,
                    "max_call_oi": call_sorted[0][1] if call_sorted else 0,
                    "max_put_strike": put_sorted[0][0] if put_sorted else 0,
                    "max_put_oi": put_sorted[0][1] if put_sorted else 0,
                    "top5_call": [{"strike": s, "oi": o} for s, o in call_sorted[:5]],
                    "top5_put": [{"strike": s, "oi": o} for s, o in put_sorted[:5]],
                    "total_call_oi": sum(otm_call.values()),
                    "total_put_oi": sum(otm_put.values()),
                    "otm_note": "僅統計價外選擇權 (OTM)",
                }

                logger.info("Options OI: max_call=%d(%d口) max_put=%d(%d口)",
                            result["max_call_strike"], result["max_call_oi"],
                            result["max_put_strike"], result["max_put_oi"])

                _set_cache("options_max_oi", result)
                return result

        except Exception as e:
            logger.debug("TAIFEX options date %s failed: %s", date_str, e)
            continue

    return {"error": "No recent options OI data"}


def get_derivatives_summary() -> dict:
    """Get comprehensive Taiwan derivatives summary."""
    pc = fetch_pc_ratio()
    futures = fetch_futures_institutional()
    options_oi = fetch_options_max_oi()

    # Scoring
    score = 0
    signals = []

    # Options OI distribution
    if "error" not in options_oi:
        max_call = options_oi.get("max_call_strike", 0)
        max_put = options_oi.get("max_put_strike", 0)
        if max_call and max_put:
            signals.append(f"最大 Call OI: {max_call:,} 點 ({options_oi['max_call_oi']:,} 口) ← 壓力")
            signals.append(f"最大 Put OI: {max_put:,} 點 ({options_oi['max_put_oi']:,} 口) ← 支撐")
            signals.append(f"預估區間: {max_put:,} ~ {max_call:,}")

    # PC Ratio scoring
    if "error" not in pc:
        oi_ratio = pc.get("oi_pc_ratio", 100)
        if oi_ratio > 130:
            score += 20  # Many puts = contrarian bullish
            signals.append(f"P/C OI {oi_ratio:.0f}% (過度悲觀→逆向看多)")
        elif oi_ratio > 110:
            score += 10
            signals.append(f"P/C OI {oi_ratio:.0f}% (偏空)")
        elif oi_ratio < 80:
            score -= 20  # Few puts = complacent → bearish
            signals.append(f"P/C OI {oi_ratio:.0f}% (過度樂觀→逆向看空)")
        elif oi_ratio < 95:
            score -= 10
            signals.append(f"P/C OI {oi_ratio:.0f}% (偏多)")
        else:
            signals.append(f"P/C OI {oi_ratio:.0f}% (中性)")

    # Futures institutional scoring
    if "error" not in futures:
        foreign_net = futures.get("foreign_net", 0)
        if foreign_net > 10000:
            score += 30
            signals.append(f"外資期貨淨多 {foreign_net:+,} 口（強力看多）")
        elif foreign_net > 3000:
            score += 15
            signals.append(f"外資期貨淨多 {foreign_net:+,} 口（偏多）")
        elif foreign_net < -10000:
            score -= 30
            signals.append(f"外資期貨淨空 {foreign_net:+,} 口（強力看空）")
        elif foreign_net < -3000:
            score -= 15
            signals.append(f"外資期貨淨空 {foreign_net:+,} 口（偏空）")
        else:
            signals.append(f"外資期貨淨部位 {foreign_net:+,} 口（中性）")

    return {
        "score": max(-100, min(100, score)),
        "pc_ratio": pc,
        "futures": futures,
        "options_oi": options_oi,
        "signals": signals,
    }
