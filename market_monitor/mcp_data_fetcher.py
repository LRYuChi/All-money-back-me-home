"""Institutional MCP Data Fetcher — optional enhancement for confidence engine.

Fetches macro economic data from paid institutional MCP servers:
- LSEG: yield curves, rate expectations
- Moody's: credit rating migration signals
- MT Newswires: real-time macro news sentiment
- FactSet: earnings revision breadth

All functions return None when API keys are not configured or on any error,
allowing the confidence engine to gracefully fall back to free data sources.

Usage:
    from market_monitor.mcp_data_fetcher import fetch_lseg_yield_curve
    result = fetch_lseg_yield_curve()  # None if no LSEG_API_KEY
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# =============================================
# Configuration
# =============================================

_DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_CACHE_FILE = _DATA_DIR / "cache" / "mcp_institutional.json"

# MCP server URLs
_LSEG_URL = "https://api.analytics.lseg.com/lfa/mcp"
_MOODYS_URL = "https://api.moodys.com/genai-ready-data/m1/mcp"
_MT_NEWSWIRES_URL = "https://vast-mcp.blueskyapi.com/mtnewswires"
_FACTSET_URL = "https://mcp.factset.com/mcp"

# Cache TTLs in seconds
_TTL_YIELD_CURVE = 4 * 3600     # 4 hours
_TTL_RATES = 4 * 3600           # 4 hours
_TTL_CREDIT = 24 * 3600         # 24 hours
_TTL_NEWS = 30 * 60             # 30 minutes
_TTL_CONSENSUS = 12 * 3600      # 12 hours

# In-memory cache: {key: (data, timestamp)}
_cache: dict[str, tuple[dict, float]] = {}


# =============================================
# Disk cache persistence
# =============================================

def _load_disk_cache() -> None:
    """Load cached MCP data from disk on startup."""
    global _cache
    try:
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            for key, entry in raw.items():
                if isinstance(entry, dict) and "data" in entry and "ts" in entry:
                    _cache[key] = (entry["data"], entry["ts"])
    except Exception:
        pass  # Corrupted cache file — start fresh


def _save_disk_cache() -> None:
    """Persist cache to disk."""
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: {"data": v[0], "ts": v[1]} for k, v in _cache.items()}
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


def _get_cached(key: str, ttl: float) -> dict | None:
    """Return cached data if fresh, else None."""
    if not _cache:
        _load_disk_cache()
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _set_cached(key: str, data: dict) -> None:
    """Store data in cache and persist."""
    _cache[key] = (data, time.time())
    _save_disk_cache()


# =============================================
# MCP HTTP Client
# =============================================

class MCPClient:
    """Thin synchronous HTTP client for remote MCP servers.

    Sends JSON-RPC 2.0 `tools/call` requests and returns the result.
    Returns None on any failure (auth, network, timeout, parse error).
    """

    def __init__(self, base_url: str, api_key: str, api_secret: str = ""):
        self._base_url = base_url
        self._headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        if api_secret:
            self._headers["X-API-Secret"] = api_secret

    def call_tool(self, tool_name: str, arguments: dict | None = None) -> dict | None:
        """Invoke an MCP tool and return the result content.

        Args:
            tool_name: MCP tool name (e.g. "macro-rates")
            arguments: Tool arguments dict

        Returns:
            Parsed result dict, or None on failure.
        """
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(self._base_url, json=payload, headers=self._headers)
                resp.raise_for_status()
                body = resp.json()

            # MCP response: {"result": {"content": [{"type": "text", "text": "..."}]}}
            result = body.get("result", {})
            content = result.get("content", [])
            if content and isinstance(content, list):
                text = content[0].get("text", "")
                if text:
                    # Try to parse as JSON
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"raw_text": text}
            return result if result else None
        except Exception as e:
            logger.debug("MCP call %s/%s failed: %s", self._base_url.split("/")[-1], tool_name, e)
            return None


# =============================================
# LSEG Data Fetchers
# =============================================

def fetch_lseg_yield_curve() -> dict | None:
    """Fetch 2s10s yield curve spread and shape from LSEG.

    Returns:
        {"spread_2s10s": float, "curve_shape": "normal"|"flat"|"inverted"}
        or None if unavailable.
    """
    api_key = os.environ.get("LSEG_API_KEY", "")
    if not api_key:
        return None

    cached = _get_cached("lseg_yield_curve", _TTL_YIELD_CURVE)
    if cached:
        return cached

    client = MCPClient(_LSEG_URL, api_key, os.environ.get("LSEG_API_SECRET", ""))
    result = client.call_tool("macro-rates", {"currency": "USD", "analysis_type": "yield_curve"})
    if not result:
        return None

    try:
        # Extract 2Y and 10Y yields from response
        # LSEG format varies; try common field names
        yields = result.get("yields", result.get("data", {}))
        y2 = None
        y10 = None

        # Try structured format
        if isinstance(yields, dict):
            y2 = yields.get("2Y", yields.get("UST_2Y", yields.get("2y")))
            y10 = yields.get("10Y", yields.get("UST_10Y", yields.get("10y")))

        # Try list format (tenors with values)
        if isinstance(yields, list):
            for item in yields:
                tenor = str(item.get("tenor", item.get("maturity", "")))
                val = item.get("yield", item.get("value"))
                if tenor in ("2Y", "2y", "2"):
                    y2 = float(val)
                elif tenor in ("10Y", "10y", "10"):
                    y10 = float(val)

        if y2 is not None and y10 is not None:
            spread = float(y10) - float(y2)
            if spread > 0.5:
                shape = "normal"
            elif spread > -0.1:
                shape = "flat"
            else:
                shape = "inverted"

            data = {
                "spread_2s10s": round(spread, 4),
                "curve_shape": shape,
                "yield_2y": round(float(y2), 4),
                "yield_10y": round(float(y10), 4),
            }
            _set_cached("lseg_yield_curve", data)
            return data

        # Fallback: use whatever LSEG returned if it has spread info
        if "spread" in result or "slope" in result:
            spread = float(result.get("spread", result.get("slope", 0)))
            shape = "normal" if spread > 0.5 else "flat" if spread > -0.1 else "inverted"
            data = {"spread_2s10s": round(spread, 4), "curve_shape": shape}
            _set_cached("lseg_yield_curve", data)
            return data

    except (ValueError, TypeError, KeyError) as e:
        logger.debug("LSEG yield curve parse error: %s", e)

    return None


def fetch_lseg_rate_expectations() -> dict | None:
    """Fetch Fed funds rate direction expectations from LSEG.

    Returns:
        {"rate_direction": "easing"|"tightening"|"hold", "ff_current": float}
        or None if unavailable.
    """
    api_key = os.environ.get("LSEG_API_KEY", "")
    if not api_key:
        return None

    cached = _get_cached("lseg_rates", _TTL_RATES)
    if cached:
        return cached

    client = MCPClient(_LSEG_URL, api_key, os.environ.get("LSEG_API_SECRET", ""))
    result = client.call_tool("analyze-swap-curve", {"currencies": ["USD"], "analysis_type": "forward_rates"})
    if not result:
        return None

    try:
        # Extract rate direction from swap curve analysis
        # Look for current vs forward rate comparison
        current_rate = result.get("current_rate", result.get("fed_funds", result.get("overnight_rate")))
        forward_rate = result.get("forward_3m", result.get("implied_rate_3m"))

        if current_rate is not None and forward_rate is not None:
            current_rate = float(current_rate)
            forward_rate = float(forward_rate)
            diff = forward_rate - current_rate

            if diff < -0.15:
                direction = "easing"
            elif diff > 0.15:
                direction = "tightening"
            else:
                direction = "hold"

            data = {
                "rate_direction": direction,
                "ff_current": round(current_rate, 4),
                "ff_forward_3m": round(forward_rate, 4),
            }
            _set_cached("lseg_rates", data)
            return data

        # Fallback: look for textual direction
        text = result.get("raw_text", result.get("summary", ""))
        if isinstance(text, str):
            text_lower = text.lower()
            if any(w in text_lower for w in ["easing", "dovish", "cut", "lower"]):
                direction = "easing"
            elif any(w in text_lower for w in ["tightening", "hawkish", "hike", "raise"]):
                direction = "tightening"
            else:
                direction = "hold"
            data = {"rate_direction": direction, "ff_current": 0}
            _set_cached("lseg_rates", data)
            return data

    except (ValueError, TypeError, KeyError) as e:
        logger.debug("LSEG rate expectations parse error: %s", e)

    return None


# =============================================
# Moody's Data Fetcher
# =============================================

def fetch_moodys_credit_signals() -> dict | None:
    """Fetch credit rating migration signals from Moody's.

    Returns:
        {"migration_score": float 0-1, "credit_direction": "tightening"|"widening"|"stable"}
        or None if unavailable.
    """
    api_key = os.environ.get("MOODYS_API_KEY", "")
    if not api_key:
        return None

    cached = _get_cached("moodys_credit", _TTL_CREDIT)
    if cached:
        return cached

    client = MCPClient(_MOODYS_URL, api_key)
    # Query broad credit migration trends
    result = client.call_tool("get_credit_rating", {
        "entity_type": "sector",
        "sector": "corporate",
        "analysis": "migration_summary",
    })
    if not result:
        return None

    try:
        # Extract upgrade/downgrade counts
        upgrades = result.get("upgrades", result.get("upgrade_count", 0))
        downgrades = result.get("downgrades", result.get("downgrade_count", 0))
        total = int(upgrades) + int(downgrades)

        if total > 0:
            # migration_score: 1.0 = all upgrades (bullish), 0.0 = all downgrades (bearish)
            score = int(upgrades) / total
        else:
            score = 0.5

        if score > 0.6:
            direction = "tightening"   # More upgrades = credit improving = risk-on
        elif score < 0.4:
            direction = "widening"     # More downgrades = credit deteriorating = risk-off
        else:
            direction = "stable"

        data = {
            "migration_score": round(score, 4),
            "credit_direction": direction,
            "upgrades": int(upgrades),
            "downgrades": int(downgrades),
        }
        _set_cached("moodys_credit", data)
        return data

    except (ValueError, TypeError, KeyError) as e:
        logger.debug("Moody's credit parse error: %s", e)

    return None


# =============================================
# MT Newswires Data Fetcher
# =============================================

def fetch_mt_newswires_macro() -> dict | None:
    """Fetch macro news sentiment from MT Newswires.

    Returns:
        {"macro_sentiment": float 0-1, "event_count": int, "key_events": list[str]}
        or None if unavailable.
    """
    api_key = os.environ.get("MT_NEWSWIRES_API_KEY", "")
    if not api_key:
        return None

    cached = _get_cached("mt_newswires_macro", _TTL_NEWS)
    if cached:
        return cached

    client = MCPClient(_MT_NEWSWIRES_URL, api_key)
    result = client.call_tool("search_news", {
        "query": "macro economy central bank interest rate inflation",
        "asset_class": "macro",
        "limit": 20,
    })
    if not result:
        return None

    try:
        articles = result.get("articles", result.get("news", result.get("results", [])))
        if not isinstance(articles, list) or not articles:
            return None

        # Simple keyword sentiment scoring
        bullish_kw = {"growth", "expansion", "rally", "surge", "strong", "beat", "dovish", "easing", "cut"}
        bearish_kw = {"recession", "contraction", "crash", "slump", "weak", "miss", "hawkish", "tightening", "hike"}

        bullish_count = 0
        bearish_count = 0
        key_events = []

        for article in articles[:20]:
            title = str(article.get("title", article.get("headline", ""))).lower()
            summary = str(article.get("summary", article.get("body", ""))).lower()
            text = f"{title} {summary}"

            bull = sum(1 for kw in bullish_kw if kw in text)
            bear = sum(1 for kw in bearish_kw if kw in text)
            bullish_count += bull
            bearish_count += bear

            # Collect key event titles
            if bull + bear > 0:
                key_events.append(article.get("title", article.get("headline", ""))[:80])

        total_signals = bullish_count + bearish_count
        if total_signals > 0:
            sentiment = 0.5 + (bullish_count - bearish_count) / total_signals * 0.4
            sentiment = max(0.1, min(0.9, sentiment))
        else:
            sentiment = 0.5

        data = {
            "macro_sentiment": round(sentiment, 4),
            "event_count": len(articles),
            "key_events": key_events[:5],
        }
        _set_cached("mt_newswires_macro", data)
        return data

    except (ValueError, TypeError, KeyError) as e:
        logger.debug("MT Newswires parse error: %s", e)

    return None


# =============================================
# FactSet Data Fetcher
# =============================================

def fetch_factset_consensus() -> dict | None:
    """Fetch S&P 500 earnings revision breadth from FactSet.

    Returns:
        {"revision_breadth": float -1 to 1, "estimate_direction": "up"|"down"|"flat"}
        or None if unavailable.
    """
    api_key = os.environ.get("FACTSET_API_KEY", "")
    if not api_key:
        return None

    cached = _get_cached("factset_consensus", _TTL_CONSENSUS)
    if cached:
        return cached

    client = MCPClient(_FACTSET_URL, api_key)
    result = client.call_tool("get_consensus_estimates", {
        "ticker": "SPX",
        "metric": "eps",
        "period": "NTM",
    })
    if not result:
        return None

    try:
        # Extract revision breadth (% of upward revisions - % of downward)
        up_revisions = result.get("up_revisions", result.get("upgrades", 0))
        down_revisions = result.get("down_revisions", result.get("downgrades", 0))
        total = int(up_revisions) + int(down_revisions)

        if total > 0:
            breadth = (int(up_revisions) - int(down_revisions)) / total
        else:
            # Try direct breadth field
            breadth = float(result.get("revision_breadth", result.get("breadth", 0)))

        breadth = max(-1.0, min(1.0, breadth))

        if breadth > 0.1:
            direction = "up"
        elif breadth < -0.1:
            direction = "down"
        else:
            direction = "flat"

        data = {
            "revision_breadth": round(breadth, 4),
            "estimate_direction": direction,
        }
        _set_cached("factset_consensus", data)
        return data

    except (ValueError, TypeError, KeyError) as e:
        logger.debug("FactSet consensus parse error: %s", e)

    return None


# =============================================
# Convenience: check which sources are active
# =============================================

def get_active_sources() -> dict[str, bool]:
    """Return which institutional MCP sources have API keys configured."""
    return {
        "lseg": bool(os.environ.get("LSEG_API_KEY")),
        "moodys": bool(os.environ.get("MOODYS_API_KEY")),
        "mt_newswires": bool(os.environ.get("MT_NEWSWIRES_API_KEY")),
        "factset": bool(os.environ.get("FACTSET_API_KEY")),
    }
