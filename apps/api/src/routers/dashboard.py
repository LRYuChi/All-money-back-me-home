"""Dashboard API — aggregated endpoint for the trading decision dashboard.

Returns all key data in a single call:
- Confidence engine score + factor breakdown
- Crypto market overview (BTC/ETH/SOL with SMC structure)
- Paper trading status + P&L
- Macro indicators (VIX, Gold, Oil, 10Y)
- Cross-market correlations
- Freqtrade bot status
"""

from __future__ import annotations

import os
import time
import urllib.request
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Cache: avoid recalculating every request
_cache: dict[str, Any] = {}
_cache_ts: float = 0
CACHE_TTL = 300  # 5 minutes


def _fetch_json(url: str, timeout: int = 10) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _get_confidence() -> dict:
    """Get confidence engine data."""
    try:
        os.environ.setdefault("FRED_API_KEY", os.environ.get("FRED_API_KEY", ""))
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        from market_monitor.confidence_engine import GlobalConfidenceEngine
        engine = GlobalConfidenceEngine()
        result = engine.calculate()
        return {
            "score": result["score"],
            "regime": result["regime"],
            "event_multiplier": result["event_multiplier"],
            "sandboxes": result["sandboxes"],
            "factors": result["factors"],
            "guidance": result["guidance"],
        }
    except Exception as e:
        logger.warning("Confidence engine error: %s", e)
        return {
            "score": 0.5, "regime": "UNKNOWN",
            "event_multiplier": 1.0,
            "sandboxes": {}, "factors": {}, "guidance": {},
        }


def _get_crypto_overview() -> list[dict]:
    """Get crypto market overview with prices and RSI."""
    symbols = [
        {"ticker": "BTC-USD", "name": "BTC"},
        {"ticker": "ETH-USD", "name": "ETH"},
        {"ticker": "SOL-USD", "name": "SOL"},
    ]
    results = []
    try:
        import yfinance as yf
        for s in symbols:
            try:
                df = yf.Ticker(s["ticker"]).history(period="5d")
                if len(df) < 2:
                    results.append({"name": s["name"], "error": "no data"})
                    continue

                close = df["Close"]
                price = float(close.iloc[-1])
                prev = float(close.iloc[-2])
                chg = (price / prev - 1) * 100

                # RSI 14
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / (loss + 1e-10)
                rsi = float((100 - 100 / (1 + rs)).iloc[-1])

                # Sparkline data: last 24 hourly-equivalent points
                sparkline = [round(float(v), 2) for v in close.tail(24).values]

                results.append({
                    "name": s["name"],
                    "price": round(price, 2),
                    "change_pct": round(chg, 2),
                    "rsi": round(rsi, 1),
                    "sparkline": sparkline,
                })
            except Exception:
                results.append({"name": s["name"], "error": "fetch failed"})
    except ImportError:
        pass
    return results


def _get_macro() -> dict:
    """Get macro indicators."""
    data = {}
    try:
        import yfinance as yf
        tickers = {
            "vix": ("^VIX", "VIX"),
            "yield_10y": ("^TNX", "10Y殖利率"),
            "gold": ("GC=F", "黃金"),
            "oil": ("CL=F", "原油"),
        }
        for key, (ticker, name) in tickers.items():
            try:
                df = yf.Ticker(ticker).history(period="5d")
                if len(df) >= 2:
                    price = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    chg = (price / prev - 1) * 100
                    data[key] = {"name": name, "price": round(price, 2), "change_pct": round(chg, 2)}
            except Exception:
                pass
    except ImportError:
        pass

    # Fear & Greed
    try:
        fg = _fetch_json("https://api.alternative.me/fng/?limit=1")
        if fg and fg.get("data"):
            val = int(fg["data"][0]["value"])
            cls = fg["data"][0].get("value_classification", "")
            data["fear_greed"] = {"value": val, "classification": cls}
    except Exception:
        pass

    # BTC Dominance
    try:
        cg = _fetch_json("https://api.coingecko.com/api/v3/global")
        if cg and cg.get("data"):
            data["btc_dominance"] = round(cg["data"]["market_cap_percentage"]["btc"], 1)
    except Exception:
        pass

    return data


def _get_trading_status() -> dict:
    """Get paper trading status from trade store."""
    try:
        from ..services.trade_store import TradeStore
        store = TradeStore()
        state = store.get_all_trades(source="scanner")
        initial = state.get("initial_capital", 300)
        capital = state.get("capital", initial)
        closed = state.get("closed_trades", [])
        wins = sum(1 for t in closed if (t.get("pnl_usd") or 0) > 0)
        total = len(closed)
        return {
            "capital": capital,
            "initial_capital": initial,
            "total_pnl": round(capital - initial, 2),
            "total_pnl_pct": round((capital - initial) / initial * 100, 2) if initial else 0,
            "open_positions": len(state.get("open_positions", [])),
            "total_trades": total,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        }
    except Exception as e:
        return {"error": str(e)}


def _get_correlations() -> dict:
    """Get cross-market correlations."""
    try:
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        from market_monitor.correlation import fetch_correlation_data, rolling_correlation
        returns = fetch_correlation_data("3mo")
        if returns is None or returns.empty:
            return {}

        corr = rolling_correlation(returns, "BTC", 30)
        if corr.empty:
            return {}

        latest = corr.iloc[-1]
        result = {}
        for pair in latest.index:
            val = float(latest[pair])
            asset = pair.replace("BTC-", "")
            strength = "強" if abs(val) > 0.7 else "中" if abs(val) > 0.4 else "弱"
            direction = "正" if val > 0 else "負"
            result[asset] = {
                "value": round(val, 2),
                "label": f"{strength}{direction}相關",
            }
        return result
    except Exception:
        return {}


def _get_freqtrade_status() -> dict:
    """Get Freqtrade bot status via Docker network."""
    # In Docker: use service name 'freqtrade'. On host: use localhost.
    ft_hosts = ["freqtrade:8080", "localhost:8080"]
    for host in ft_hosts:
        try:
            import base64
            auth = base64.b64encode(b"freqtrade:freqtrade").decode()
            headers = {"Authorization": f"Basic {auth}"}

            req = urllib.request.Request(
                f"http://{host}/api/v1/show_config", headers=headers,
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                config = json.loads(resp.read())

            req2 = urllib.request.Request(
                f"http://{host}/api/v1/profit", headers=headers,
            )
            with urllib.request.urlopen(req2, timeout=5) as resp2:
                profit = json.loads(resp2.read())

            return {
                "state": config.get("state", "unknown"),
                "strategy": config.get("strategy", "unknown"),
                "dry_run": config.get("dry_run", True),
                "trading_mode": config.get("trading_mode", ""),
                "trade_count": profit.get("trade_count", 0),
                "profit": round(profit.get("profit_all_coin", 0), 2),
            }
        except Exception:
            continue

    return {"state": "offline", "strategy": "unknown"}


def _get_next_killzone() -> dict:
    """Calculate next killzone window."""
    now = datetime.utcnow()
    hour = now.hour
    killzones = [
        (7, 10, "倫敦開盤"),
        (12, 14, "紐約開盤"),
        (15, 17, "倫敦收盤"),
    ]
    for start, end, name in killzones:
        if hour < start:
            return {"name": name, "starts_in_hours": start - hour, "utc_start": f"{start}:00"}
        if start <= hour <= end:
            return {"name": name, "active": True, "utc_start": f"{start}:00"}

    # Next day London
    return {"name": "倫敦開盤", "starts_in_hours": 24 - hour + 7, "utc_start": "07:00"}


@router.get("")
async def get_dashboard() -> dict[str, Any]:
    """Aggregated dashboard data — cached 5 minutes."""
    global _cache, _cache_ts

    now = time.time()
    if now - _cache_ts < CACHE_TTL and _cache:
        return _cache

    data = {
        "timestamp": datetime.utcnow().isoformat(),
        "confidence": _get_confidence(),
        "crypto": _get_crypto_overview(),
        "trading": _get_trading_status(),
        "macro": _get_macro(),
        "correlations": _get_correlations(),
        "freqtrade": _get_freqtrade_status(),
        "next_killzone": _get_next_killzone(),
    }

    _cache = data
    _cache_ts = now
    return data
