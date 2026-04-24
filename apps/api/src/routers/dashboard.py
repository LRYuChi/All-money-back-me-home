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
        try:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        except IndexError:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
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
            "institutional_sources": result.get("institutional_sources", {}),
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
    if score >= 0.7:
        regime = "AGGRESSIVE"
    elif score >= 0.5:
        regime = "NORMAL"
    elif score >= 0.35:
        regime = "CAUTIOUS"
    elif score >= 0.2:
        regime = "DEFENSIVE"
    else:
        regime = "HIBERNATE"

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


def _ft_api(path: str) -> dict | list | None:
    """Query Freqtrade REST API (shared helper)."""
    import base64
    # Try env vars first, then read from config_secrets.json
    ft_user = os.environ.get("FT_USER", "freqtrade")
    ft_pass = os.environ.get("FT_PASS", "")
    if not ft_pass:
        # Read from config file (Docker volume or local)
        for cfg_path in ["/app/config/config_secrets.json", "/freqtrade/config/config_secrets.json", "/opt/ambmh/config/freqtrade/config_secrets.json"]:
            try:
                with open(cfg_path) as f:
                    cfg = json.load(f)
                ft_pass = cfg.get("api_server", {}).get("password", "")
                ft_user = cfg.get("api_server", {}).get("username", ft_user)
                if ft_pass:
                    break
            except Exception:
                continue
    if not ft_pass:
        ft_pass = "freqtrade"

    auth = base64.b64encode(f"{ft_user}:{ft_pass}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    for host in ["freqtrade:8080", "localhost:8080"]:
        try:
            req = urllib.request.Request(f"http://{host}/api/v1/{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            continue
    return None


def _get_trading_status() -> dict:
    """Get trading status from Freqtrade API (live data, not scanner)."""
    try:
        profit = _ft_api("profit")
        balance = _ft_api("balance")
        status = _ft_api("status")
        if profit and balance:
            total_bal = balance.get("total", 1000)
            initial = 1000  # dry_run_wallet
            return {
                "capital": round(total_bal, 2),
                "initial_capital": initial,
                "total_pnl": round(profit.get("profit_all_coin", 0), 2),
                "total_pnl_pct": round(profit.get("profit_all_percent", 0), 2),
                "open_positions": len(status) if isinstance(status, list) else 0,
                "total_trades": profit.get("trade_count", 0),
                "win_rate": round(
                    profit.get("winning_trades", 0) / max(profit.get("closed_trade_count", 1), 1) * 100, 1
                ),
            }
    except Exception:
        pass
    # Fallback to scanner TradeStore
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
            "capital": capital, "initial_capital": initial,
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
        try:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[4]))
        except IndexError:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent))
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
        watchlist_codes = ["2330", "2317", "2454", "2382"]
        fundamentals = twse.get_watchlist_fundamentals(watchlist_codes)
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

        # Institutional investors (三大法人) — market-level in 億元, per-stock in shares
        try:
            inst = twse.get_institutional_daily()
            if inst:
                result["institutional"] = {
                    "date": inst.get("date", ""),
                    "foreign": inst.get("foreign", {}),    # 億元
                    "trust": inst.get("trust", {}),        # 億元
                    "dealer": inst.get("dealer", {}),      # 億元
                    "total": inst.get("total", {}),        # 億元
                }
                # Per-stock institutional data for watchlist (in shares)
                inst_stocks = twse.get_institutional_for_stocks(watchlist_codes)
                if inst_stocks:
                    result["institutional_stocks"] = [
                        {
                            "code": s["code"],
                            "name": s["name"],
                            "foreign_net": s["foreign_net"],
                            "trust_net": s["trust_net"],
                            "dealer_net": s["dealer_net"],
                            "total_net": s["total_net"],
                        }
                        for s in inst_stocks
                    ]
        except Exception as e:
            logger.warning("Institutional data error: %s", e)

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
        _ft_creds = f"{os.environ.get('FT_USER', 'freqtrade')}:{os.environ.get('FT_PASS', 'freqtrade')}"
        auth = base64.b64encode(_ft_creds.encode()).decode()
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
    """Get Freqtrade bot status via Docker network."""
    # Try Docker service name first, then localhost
    hosts = ["freqtrade:8080", "localhost:8080"]
    for host in hosts:
        status = _query_ft_bot(host)
        if status:
            return status
    return {"state": "offline", "strategy": "unknown"}


def _get_crypto_environment() -> dict:
    """Get crypto-specific environment scores using free APIs."""
    try:
        import sys
        # Add paths where market_monitor might be
        for p in ["/app", "/opt/ambmh"]:
            if p not in sys.path:
                sys.path.insert(0, p)
        from market_monitor.crypto_environment import CryptoEnvironmentEngine
        cg_key = os.environ.get("COINGLASS_API_KEY")
        engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
        results = {}
        for sym in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]:
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


@router.get("/ft-trades")
async def get_ft_trades():
    """Freqtrade 交易數據 — open positions + closed trades + bot meta."""
    status = _ft_api("status") or []
    trades_resp = _ft_api("trades?limit=50") or {"trades": []}
    profit = _ft_api("profit") or {}
    balance = _ft_api("balance") or {"total": 1000}
    cfg = _ft_api("show_config") or {}
    whitelist = _ft_api("whitelist") or {}

    all_trades = trades_resp.get("trades", []) if isinstance(trades_resp, dict) else []
    open_positions = []
    closed_trades = []

    for t in (status if isinstance(status, list) else []):
        open_positions.append({
            "symbol": t.get("pair", "?"),
            "direction": "short" if t.get("is_short") else "long",
            "entry_price": t.get("open_rate", 0),
            "stop_loss": t.get("stop_loss", 0),
            "take_profit_levels": [],
            "position_size_usd": t.get("stake_amount", 0),
            "leverage": t.get("leverage", 1),
            "confidence": 0,
            "reason": t.get("enter_tag", ""),
            "entry_time": t.get("open_date", ""),
            "current_rate": t.get("current_rate", 0),
            "profit_pct": t.get("profit_pct", 0),
            "profit_abs": t.get("profit_abs", 0),
        })

    for t in all_trades:
        if t.get("is_open"):
            continue
        closed_trades.append({
            "symbol": t.get("pair", "?"),
            "direction": "short" if t.get("is_short") else "long",
            "entry_price": t.get("open_rate", 0),
            "exit_price": t.get("close_rate", 0),
            "pnl_pct": t.get("profit_pct", 0) or 0,
            "pnl_usd": t.get("profit_abs", 0) or 0,
            "exit_reason": t.get("exit_reason", "?"),
            "r_multiple": None,
            "leverage": t.get("leverage", 1),
            "duration_bars": t.get("trade_duration", 0),
            "entry_time": t.get("open_date", ""),
            "exit_time": t.get("close_date", ""),
        })

    capital = balance.get("total", 1000)
    initial = 1000
    total_pnl = profit.get("profit_all_coin", 0)
    total_closed = profit.get("closed_trade_count", 0) or len(closed_trades)
    wins = profit.get("winning_trades", 0)
    unrealized_pnl = sum((p.get("profit_abs") or 0) for p in open_positions)

    pairs = whitelist.get("whitelist", []) if isinstance(whitelist, dict) else []
    bot_meta = {
        "state": cfg.get("state"),
        "dry_run": cfg.get("dry_run"),
        "strategy": cfg.get("strategy"),
        "timeframe": cfg.get("timeframe"),
        "exchange": cfg.get("exchange"),
        "trading_mode": cfg.get("trading_mode"),
        "max_open_trades": cfg.get("max_open_trades"),
        "stake_amount": cfg.get("stake_amount"),
        "stake_currency": cfg.get("stake_currency"),
        "pairs": pairs,
        "pairs_count": len(pairs),
        "bot_start_timestamp": profit.get("bot_start_timestamp"),
        "bot_start_date": profit.get("bot_start_date"),
    }

    return {
        "capital": round(capital, 2),
        "initial_capital": initial,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / initial * 100, 2) if initial else 0,
        "realized_pnl": round(profit.get("profit_closed_coin", 0) or 0, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "open_positions": open_positions,
        "closed_trades": closed_trades,
        "win_rate": round(wins / max(total_closed, 1) * 100, 1),
        "total_trades": profit.get("trade_count", 0),
        "winning_trades": wins,
        "losing_trades": profit.get("losing_trades", 0),
        "best_pair": profit.get("best_pair", ""),
        "best_pair_pnl": round(profit.get("best_pair_profit_abs", 0) or 0, 2),
        "max_drawdown": round(profit.get("max_drawdown", 0) or 0, 4),
        "max_drawdown_abs": round(profit.get("max_drawdown_abs", 0) or 0, 2),
        "profit_factor": profit.get("profit_factor"),
        "sharpe": profit.get("sharpe"),
        "bot": bot_meta,
        "last_updated": datetime.utcnow().isoformat(),
    }


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


# =============================================
# Trade Journal API
# =============================================

def _read_journal(limit: int = 50) -> list[dict]:
    """Read trade_journal.jsonl (newest first)."""
    from pathlib import Path
    journal_path = Path(os.environ.get("DATA_DIR", "/app/data")) / "trade_journal.jsonl"
    if not journal_path.exists():
        return []
    try:
        with open(journal_path) as f:
            lines = f.readlines()
        entries = []
        for line in reversed(lines):
            try:
                entries.append(json.loads(line.strip()))
            except Exception:
                continue
            if len(entries) >= limit:
                break
        return entries
    except Exception:
        return []


@router.get("/journal")
async def get_trade_journal(limit: int = 30):
    """交易日誌 — ENTRY/EXIT 配對，含 grade、conditions、R-multiple."""
    entries = _read_journal(limit * 2)
    if not entries:
        return {"trades": [], "stats": {}}

    # Pair ENTRY and EXIT
    exits = [e for e in entries if e.get("event") == "EXIT"]
    entry_map: dict[str, dict] = {}
    for e in entries:
        if e.get("event") == "ENTRY":
            key = e.get("pair", "")
            if key not in entry_map:
                entry_map[key] = e

    trades = []
    grade_stats: dict[str, dict] = {}
    for ex in exits[:limit]:
        pair = ex.get("pair", "?")
        en = entry_map.get(pair, {})
        pnl = ex.get("pnl_usd", 0)
        grade = en.get("grade", "?")

        if grade not in grade_stats:
            grade_stats[grade] = {"wins": 0, "losses": 0, "pnl": 0}
        if pnl > 0:
            grade_stats[grade]["wins"] += 1
        else:
            grade_stats[grade]["losses"] += 1
        grade_stats[grade]["pnl"] += pnl

        trades.append({
            "pair": pair,
            "side": en.get("side", ex.get("side", "?")),
            "grade": grade,
            "entry_price": en.get("entry_price", 0),
            "exit_price": ex.get("exit_price", 0),
            "confidence_entry": en.get("confidence", 0),
            "confidence_exit": ex.get("confidence_at_exit", 0),
            "conditions": en.get("conditions", {}),
            "r_multiple": ex.get("r_multiple", 0),
            "pnl_pct": ex.get("pnl_pct", 0),
            "pnl_usd": pnl,
            "duration_min": ex.get("duration_min", 0),
            "exit_reason": ex.get("exit_reason", "?"),
            "slippage_pct": ex.get("slippage_pct", 0),
            "entry_ts": en.get("ts", ""),
            "exit_ts": ex.get("ts", ""),
            "leverage": en.get("leverage", 1),
            "atr_pct": en.get("atr_pct", 0),
            "macro_regime": en.get("macro_regime", ""),
        })

    # Compute stats per grade
    for g in grade_stats.values():
        t = g["wins"] + g["losses"]
        g["total"] = t
        g["win_rate"] = round(g["wins"] / t * 100, 1) if t > 0 else 0

    return {"trades": trades, "grade_stats": grade_stats}


@router.get("/guards")
async def get_guard_status():
    """Guard Pipeline 狀態 — daily loss、streak、cooldown、drawdown."""
    from pathlib import Path
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))

    result: dict[str, Any] = {"guards": {}, "bot_state": {}}

    # Guard state
    guard_path = data_dir / "guard_state.json"
    if guard_path.exists():
        try:
            with open(guard_path) as f:
                result["guards"] = json.load(f)
        except Exception:
            pass

    # Bot state (agent flags, daily counters)
    bot_path = data_dir / "reports" / "bot_state.json"
    if bot_path.exists():
        try:
            with open(bot_path) as f:
                state = json.load(f)
            result["bot_state"] = {
                "guard_rejections_today": state.get("guard_rejections_today", 0),
                "signals_generated_today": state.get("signals_generated_today", 0),
                "circuit_breaker_activations": state.get("circuit_breaker_activations", 0),
                "consecutive_wins": state.get("consecutive_wins", 0),
                "consecutive_losses": state.get("consecutive_losses", 0),
                "last_confidence_score": state.get("last_confidence_score", 0),
                "last_confidence_regime": state.get("last_confidence_regime", ""),
                "agent_pause_entries": state.get("agent_pause_entries", False),
                "agent_risk_level": state.get("agent_risk_level", "normal"),
            }
        except Exception:
            pass

    return result


@router.get("/equity-curve")
async def get_equity_curve():
    """資金曲線 — 從 Freqtrade 交易記錄計算累積損益。"""
    trades = _ft_api("/api/v1/trades?limit=500")
    if not trades:
        return {"curve": [], "total_profit": 0}

    trade_list = trades.get("trades", []) if isinstance(trades, dict) else trades
    curve = []
    cumulative = 0
    for t in trade_list:
        if t.get("close_date") and t.get("profit_abs") is not None:
            cumulative += t["profit_abs"]
            curve.append({
                "date": t["close_date"],
                "profit": round(cumulative, 2),
                "trade_profit": round(t["profit_abs"], 2),
                "pair": t.get("pair", ""),
                "side": "short" if t.get("is_short") else "long",
            })

    return {"curve": curve, "total_profit": round(cumulative, 2)}


@router.get("/supertrend-signals")
async def get_supertrend_signals():
    """Supertrend 4L 即時信號 — 7 幣種的四層方向 + 品質分數。"""
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT", "AVAX/USDT:USDT",
             "NEAR/USDT:USDT", "ATOM/USDT:USDT", "ADA/USDT:USDT", "DOT/USDT:USDT"]

    signals = []
    for pair in pairs:
        candles = _ft_api(f"/api/v1/pair_candles?pair={pair}&timeframe=15m&limit=1")
        if not candles:
            continue
        cols = candles.get("columns", [])
        rows = candles.get("data", [])
        if not rows:
            continue
        r = dict(zip(cols, rows[-1]))

        st_15m = int(r.get("st_trend", 0))
        st_1h = int(r.get("st_1h", 0))
        st_1d = int(r.get("st_1d", 0))
        dir_4h = float(r.get("dir_4h_score", 0))
        adx = round(float(r.get("adx", 0)), 1)
        tq = round(float(r.get("trend_quality", 0)), 2)
        ab = bool(r.get("all_bullish", False))
        ae = bool(r.get("all_bearish", False))

        if ab and st_15m == 1:
            status = "confirmed_long"
        elif ae and st_15m == -1:
            status = "confirmed_short"
        elif ab and st_15m == -1:
            status = "scout_long"
        elif ae and st_15m == 1:
            status = "scout_short"
        elif ab:
            status = "bullish"
        elif ae:
            status = "bearish"
        else:
            status = "neutral"

        signals.append({
            "pair": pair,
            "symbol": pair.split("/")[0],
            "close": round(float(r.get("close", 0)), 2),
            "st_15m": st_15m,
            "st_1h": st_1h,
            "st_1d": st_1d,
            "dir_4h": round(dir_4h, 2),
            "adx": adx,
            "trend_quality": tq,
            "all_bullish": ab,
            "all_bearish": ae,
            "status": status,
            "adx_ok": adx > 25,
            "quality_ok": tq > 0.5,
        })

    return {"signals": signals, "timestamp": datetime.utcnow().isoformat()}
