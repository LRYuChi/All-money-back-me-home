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

    # Fallback: build from available macro data
    score = 0.5
    regime = "CAUTIOUS"
    sandboxes = {"macro": 0.4, "sentiment": 0.5, "capital": 0.5, "haven": 0.5}

    # Try to get VIX/Fear&Greed for better fallback
    try:
        import yfinance as yf
        vix_df = yf.Ticker("^VIX").history(period="5d")
        if not vix_df.empty:
            vix_val = float(vix_df["Close"].iloc[-1])
            sandboxes["sentiment"] = max(0, min(1, 1 - vix_val / 50))
    except Exception:
        pass

    try:
        fg = _fetch_json("https://api.alternative.me/fng/?limit=1")
        if fg and fg.get("data"):
            fg_val = int(fg["data"][0]["value"])
            sandboxes["macro"] = fg_val / 100
    except Exception:
        pass

    avg = sum(sandboxes.values()) / len(sandboxes)
    score = round(avg, 2)
    if score >= 0.7: regime = "AGGRESSIVE"
    elif score >= 0.5: regime = "NORMAL"
    elif score >= 0.35: regime = "CAUTIOUS"
    elif score >= 0.2: regime = "DEFENSIVE"
    else: regime = "HIBERNATE"

    guidance = {
        "position_pct": 100 if score >= 0.7 else 75 if score >= 0.5 else 50 if score >= 0.35 else 25,
        "leverage": round(1.0 + 2.0 * score ** 2, 1),
        "threshold_mult": round(1.0 / max(score, 0.1), 1),
    }

    return {
        "score": score, "regime": regime,
        "event_multiplier": 1.0,
        "sandboxes": sandboxes,
        "factors": {},
        "guidance": guidance,
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


def _get_tw_market() -> dict:
    """Get Taiwan market data from TWSE OpenAPI (official source)."""
    try:
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        from market_monitor.fetchers.twse_openapi import TWSEOpenAPIClient
        twse = TWSEOpenAPIClient()

        result: dict = {}

        # TAIEX from official source
        taiex = twse.get_taiex()
        if taiex:
            result["taiex"] = {
                "close": taiex["close"],
                "change": taiex["change"],
                "change_pct": taiex["change_pct"],
            }

        # Key sector indices
        sector_keywords = ["半導體", "電子", "金融保險", "航運"]
        sectors = []
        for idx in twse.get_sector_indices():
            if any(k in idx["name"] for k in sector_keywords) and idx["close"] is not None:
                sectors.append({
                    "name": idx["name"],
                    "close": idx["close"],
                    "change_pct": idx["change_pct"],
                })
        if sectors:
            result["sectors"] = sectors

        # Watchlist fundamentals
        fundamentals = twse.get_watchlist_fundamentals(["2330", "2317", "2454", "2382"])
        if fundamentals:
            result["fundamentals"] = [
                {
                    "code": f["code"],
                    "name": f["name"],
                    "pe_ratio": f["pe_ratio"],
                    "pb_ratio": f["pb_ratio"],
                    "dividend_yield": f["dividend_yield"],
                }
                for f in fundamentals
            ]

        return result
    except Exception as e:
        logger.warning("TWSE market data error: %s", e)
        return {}


def _get_correlations() -> dict:
    """Get cross-market correlations using yfinance directly."""
    try:
        import yfinance as yf
        import pandas as pd

        tickers = {"SPY": "SPY", "Gold": "GC=F", "Oil": "CL=F", "TWII": "^TWII"}
        btc = yf.Ticker("BTC-USD").history(period="3mo")
        if btc.empty:
            return {}

        btc_ret = btc["Close"].pct_change().dropna()
        btc_ret.index = btc_ret.index.normalize()

        result = {}
        for name, ticker in tickers.items():
            try:
                df = yf.Ticker(ticker).history(period="3mo")
                if df.empty:
                    continue
                asset_ret = df["Close"].pct_change().dropna()
                asset_ret.index = asset_ret.index.normalize()

                combined = pd.DataFrame({"btc": btc_ret, "asset": asset_ret}).dropna()
                if len(combined) < 20:
                    continue

                corr_val = float(combined["btc"].rolling(30).corr(combined["asset"]).dropna().iloc[-1])
                strength = "強" if abs(corr_val) > 0.7 else "中" if abs(corr_val) > 0.4 else "弱"
                direction = "正" if corr_val > 0 else "負"
                result[name] = {
                    "value": round(corr_val, 2),
                    "label": f"{strength}{direction}相關",
                }
            except Exception:
                continue
        return result
    except Exception:
        return {}


def _query_ft_bot(host: str) -> dict | None:
    """Query a single Freqtrade bot via REST API."""
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
        return None


def _get_freqtrade_status() -> dict:
    """Get Freqtrade bot status — supports multiple bots."""
    # Bot definitions: (name, Docker host, localhost fallback)
    bots = [
        ("trend", "freqtrade-trend:8080", "localhost:8080"),
        ("scalp", "freqtrade-scalp:8081", "localhost:8081"),
    ]

    result = {}
    for name, docker_host, local_host in bots:
        status = _query_ft_bot(docker_host) or _query_ft_bot(local_host)
        result[name] = status or {"state": "offline", "strategy": "unknown"}

    return result


def _get_crypto_environment() -> dict:
    """Get crypto-specific environment scores."""
    try:
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        from market_monitor.crypto_environment import CryptoEnvironmentEngine
        engine = CryptoEnvironmentEngine()
        results = {}
        for sym in ["BTC", "ETH", "SOL"]:
            r = engine.calculate(sym)
            results[sym] = {
                "score": r["score"],
                "regime": r["regime"],
                "sandboxes": r["sandboxes"],
                "factors": {k: {"score": v.get("score", 0), "signal": v.get("signal", "")}
                           for k, v in r.get("factors", {}).items()},
            }
        return results
    except Exception as e:
        logger.warning("Crypto environment error: %s", e)
        return {}


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
        "crypto_env": _get_crypto_environment(),
        "crypto": _get_crypto_overview(),
        "trading": _get_trading_status(),
        "macro": _get_macro(),
        "tw_market": _get_tw_market(),
        "correlations": _get_correlations(),
        "freqtrade": _get_freqtrade_status(),
        "next_killzone": _get_next_killzone(),
    }

    _cache = data
    _cache_ts = now
    return data
