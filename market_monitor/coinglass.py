"""CoinGlass API Client — crypto derivatives data for confidence engine & strategy.

Provides institutional-grade derivatives data:
1. OI-Weighted Funding Rate — more accurate than single-exchange FR
2. Open Interest OHLC — validate BOS/CHoCH (OI↑=real, OI↓=fake)
3. Long/Short Ratio (top traders) — institutional positioning
4. CVD (Cumulative Volume Delta) — buy/sell pressure
5. Liquidation data — sweep detection for SMC

API docs: https://docs.coinglass.com/reference/futures-funding-rate-ohlc-history
Rate limit: 30 req/min (free), 100 req/min (paid)
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

# Module-level cache
_cache: dict[str, Any] = {}
_cache_ts: dict[str, float] = {}
CACHE_TTL = 300  # 5 min default


def _fetch(
    endpoint: str,
    api_key: str,
    params: dict[str, str] | None = None,
    cache_key: str | None = None,
    cache_ttl: int = CACHE_TTL,
) -> Any:
    """Fetch from CoinGlass API with caching and error handling."""
    if cache_key and cache_key in _cache:
        if time.time() - _cache_ts.get(cache_key, 0) < cache_ttl:
            return _cache[cache_key]

    base_url = "https://open-api-v3.coinglass.com"
    url = f"{base_url}{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "accept": "application/json",
                "CG-API-KEY": api_key,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        code = data.get("code")
        if code not in ("0", 0):
            msg = data.get("msg", "")
            if code == "40001" or "Upgrade" in str(msg):
                logger.info("CoinGlass endpoint %s requires plan upgrade", endpoint)
            else:
                logger.warning("CoinGlass API error: %s — %s", code, msg)
            return None

        result = data.get("data")
        if cache_key and result is not None:
            _cache[cache_key] = result
            _cache_ts[cache_key] = time.time()
        return result

    except Exception as e:
        logger.warning("CoinGlass fetch failed (%s): %s", endpoint, e)
        return None


class CoinGlassClient:
    """CoinGlass API client for derivatives market data."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    # ------------------------------------------------------------------
    # 1. OI-Weighted Funding Rate
    # ------------------------------------------------------------------

    def get_funding_rate(self, symbol: str = "BTC") -> dict | None:
        """Get current aggregated funding rate across exchanges.

        Returns latest funding rate with OI-weighted average.
        """
        data = _fetch(
            "/api/futures/funding-rates-oi-weight",
            self.api_key,
            params={"symbol": symbol},
            cache_key=f"cg_fr_{symbol}",
        )
        if not data:
            return None

        # data is a list of exchanges with their funding rates
        if isinstance(data, list) and len(data) > 0:
            # Calculate OI-weighted average
            total_oi = 0.0
            weighted_fr = 0.0
            exchange_data = []
            for item in data:
                oi = float(item.get("openInterest", 0) or 0)
                fr = float(item.get("rate", 0) or 0)
                total_oi += oi
                weighted_fr += fr * oi
                exchange_data.append({
                    "exchange": item.get("exchangeName", ""),
                    "rate": fr,
                    "oi": oi,
                })

            avg_fr = weighted_fr / total_oi if total_oi > 0 else 0
            return {
                "weighted_rate": avg_fr,
                "total_oi_usd": total_oi,
                "exchanges": exchange_data[:5],  # Top 5
            }

        return None

    def get_funding_rate_history(
        self, symbol: str = "BTC", interval: str = "h8", limit: int = 30
    ) -> list[dict] | None:
        """Get funding rate OHLC history.

        interval: h1, h2, h4, h8 (default 8h = standard funding interval)
        """
        data = _fetch(
            "/api/futures/funding-rate/ohlc-history",
            self.api_key,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            cache_key=f"cg_fr_hist_{symbol}_{interval}",
        )
        if not data or not isinstance(data, list):
            return None
        return data

    # ------------------------------------------------------------------
    # 2. Open Interest
    # ------------------------------------------------------------------

    def get_open_interest(self, symbol: str = "BTC") -> dict | None:
        """Get current aggregated open interest across exchanges."""
        data = _fetch(
            "/api/futures/openInterest",
            self.api_key,
            params={"symbol": symbol},
            cache_key=f"cg_oi_{symbol}",
        )
        if not data:
            return None

        if isinstance(data, list) and len(data) > 0:
            total_oi = sum(float(d.get("openInterest", 0) or 0) for d in data)
            return {
                "total_oi_usd": total_oi,
                "exchange_count": len(data),
            }
        return None

    def get_oi_history(
        self, symbol: str = "BTC", interval: str = "h1", limit: int = 24
    ) -> list[dict] | None:
        """Get open interest OHLC history for trend analysis.

        Used to validate BOS/CHoCH:
        - OI rising during breakout = real (new money entering)
        - OI falling during breakout = fake (just liquidations)
        """
        data = _fetch(
            "/api/futures/openInterest/ohlc-history",
            self.api_key,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            cache_key=f"cg_oi_hist_{symbol}_{interval}",
        )
        if not data or not isinstance(data, list):
            return None
        return data

    # ------------------------------------------------------------------
    # 3. Long/Short Ratio
    # ------------------------------------------------------------------

    def get_long_short_ratio(
        self, symbol: str = "BTC", interval: str = "h4", limit: int = 10
    ) -> list[dict] | None:
        """Get global long/short account ratio history.

        Contrarian indicator:
        - High long ratio (>1.5) = crowd is long = bearish signal
        - High short ratio (<0.7) = crowd is short = bullish signal
        """
        data = _fetch(
            "/api/futures/globalLongShortAccountRatio/history",
            self.api_key,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            cache_key=f"cg_ls_{symbol}_{interval}",
        )
        if not data or not isinstance(data, list):
            return None
        return data

    def get_top_trader_ls_ratio(
        self, symbol: str = "BTC", interval: str = "h4", limit: int = 10
    ) -> list[dict] | None:
        """Get top trader long/short ratio (institutional positioning).

        More signal than retail L/S ratio — top traders on Binance/OKX.
        """
        data = _fetch(
            "/api/futures/topLongShortAccountRatio/history",
            self.api_key,
            params={"symbol": symbol, "interval": interval, "limit": str(limit)},
            cache_key=f"cg_top_ls_{symbol}_{interval}",
        )
        if not data or not isinstance(data, list):
            return None
        return data

    # ------------------------------------------------------------------
    # 4. Liquidations
    # ------------------------------------------------------------------

    def get_liquidation_data(self, symbol: str = "BTC") -> dict | None:
        """Get recent liquidation aggregation.

        Used for sweep detection in SMC:
        - Large liquidation cluster = liquidity swept
        - Post-sweep = potential reversal zone
        """
        data = _fetch(
            "/api/futures/liquidation/detail",
            self.api_key,
            params={"symbol": symbol},
            cache_key=f"cg_liq_{symbol}",
            cache_ttl=120,  # 2 min cache for liquidation data
        )
        return data

    def get_liquidation_heatmap(
        self, symbol: str = "BTC", interval: str = "h1"
    ) -> dict | None:
        """Get liquidation heatmap data — shows where liquidity pools are.

        Critical for SMC:
        - Liquidity pools above/below = likely sweep targets
        - Price tends to move toward liquidity before reversing
        """
        data = _fetch(
            "/api/futures/liquidation/v2/heatmap",
            self.api_key,
            params={"symbol": symbol, "interval": interval},
            cache_key=f"cg_heatmap_{symbol}_{interval}",
            cache_ttl=600,  # 10 min cache for heatmap
        )
        return data

    # ------------------------------------------------------------------
    # 5. Aggregated Derivatives Score
    # ------------------------------------------------------------------

    def calculate_derivatives_score(self, symbol: str = "BTC") -> dict:
        """Calculate an aggregated derivatives score for the confidence engine.

        Components (weighted):
        - OI-Weighted Funding Rate (30%): contrarian — extreme positive = bearish
        - OI Trend (25%): rising OI = conviction, falling = unwinding
        - Long/Short Ratio (25%): contrarian — crowd long = bearish
        - Top Trader L/S (20%): institutional direction confirmation

        Returns: score 0.0-1.0 with factor breakdown
        """
        import numpy as np

        factors = {}

        # 1. Funding Rate (30%)
        fr_data = self.get_funding_rate(symbol)
        if fr_data:
            fr = fr_data["weighted_rate"]
            # Contrarian: negative FR = shorts paying = bullish
            fr_score = float(np.clip(0.5 - fr * 400, 0, 1))
            signal = "neutral"
            if fr > 0.0005:
                signal = f"overleveraged longs ({fr*100:.4f}%) ⚠️"
            elif fr < -0.0003:
                signal = f"shorts paying ({fr*100:.4f}%) → bullish"
            elif fr < 0:
                signal = f"slightly bullish ({fr*100:.4f}%)"
            factors["oi_weighted_fr"] = {
                "score": round(fr_score, 4),
                "value": round(fr * 100, 4),
                "signal": signal,
                "total_oi": fr_data["total_oi_usd"],
            }
        else:
            factors["oi_weighted_fr"] = {"score": 0.5, "signal": "no data"}

        # 2. OI Trend (25%)
        oi_hist = self.get_oi_history(symbol, interval="h1", limit=24)
        if oi_hist and len(oi_hist) >= 2:
            # Extract OI values from history
            oi_values = []
            for item in oi_hist:
                oi_val = item.get("o") or item.get("openInterest") or item.get("c")
                if oi_val is not None:
                    oi_values.append(float(oi_val))

            if len(oi_values) >= 2:
                change_pct = (oi_values[-1] - oi_values[0]) / max(oi_values[0], 1) * 100
                oi_score = float(np.clip(0.5 + change_pct * 5, 0, 1))
                signal = "stable"
                if change_pct > 3:
                    signal = f"OI rising +{change_pct:.1f}% → conviction"
                elif change_pct < -3:
                    signal = f"OI falling {change_pct:.1f}% → unwinding"
                factors["oi_trend"] = {
                    "score": round(oi_score, 4),
                    "value": round(change_pct, 2),
                    "signal": signal,
                }
            else:
                factors["oi_trend"] = {"score": 0.5, "signal": "insufficient data"}
        else:
            factors["oi_trend"] = {"score": 0.5, "signal": "no data"}

        # 3. Long/Short Ratio (25%)
        ls_data = self.get_long_short_ratio(symbol, limit=10)
        if ls_data and len(ls_data) > 0:
            latest = ls_data[-1]
            ratio = float(latest.get("longShortRatio", 1.0) or 1.0)
            # Contrarian: high long = crowd long = bearish
            ls_score = float(np.clip(1.0 - ratio / 2.0, 0, 1))
            signal = "neutral"
            if ratio > 1.5:
                signal = f"crowd heavily long ({ratio:.2f}) ⚠️"
            elif ratio < 0.7:
                signal = f"crowd heavily short ({ratio:.2f}) → bullish"
            factors["long_short_ratio"] = {
                "score": round(ls_score, 4),
                "value": round(ratio, 4),
                "signal": signal,
            }
        else:
            factors["long_short_ratio"] = {"score": 0.5, "signal": "no data"}

        # 4. Top Trader L/S (20%)
        top_ls = self.get_top_trader_ls_ratio(symbol, limit=5)
        if top_ls and len(top_ls) > 0:
            latest = top_ls[-1]
            ratio = float(latest.get("longShortRatio", 1.0) or 1.0)
            # Confirming: top traders long = bullish signal
            top_score = float(np.clip(ratio / 2.0, 0, 1))
            signal = "neutral"
            if ratio > 1.3:
                signal = f"institutions long ({ratio:.2f}) → bullish"
            elif ratio < 0.8:
                signal = f"institutions short ({ratio:.2f}) → bearish"
            factors["top_trader_ls"] = {
                "score": round(top_score, 4),
                "value": round(ratio, 4),
                "signal": signal,
            }
        else:
            factors["top_trader_ls"] = {"score": 0.5, "signal": "no data"}

        # Weighted score
        weights = {
            "oi_weighted_fr": 0.30,
            "oi_trend": 0.25,
            "long_short_ratio": 0.25,
            "top_trader_ls": 0.20,
        }
        score = sum(
            weights[k] * factors[k]["score"]
            for k in weights
        )
        score = float(np.clip(score, 0, 1))

        return {
            "score": round(score, 4),
            "factors": factors,
        }


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    import os

    api_key = os.environ.get("COINGLASS_API_KEY", "")
    if not api_key:
        print("Set COINGLASS_API_KEY environment variable")
        return

    client = CoinGlassClient(api_key)

    for sym in ["BTC", "ETH", "SOL"]:
        print(f"\n{'='*50}")
        print(f"  {sym} — CoinGlass Derivatives")
        print(f"{'='*50}")

        result = client.calculate_derivatives_score(sym)
        print(f"  Score: {result['score']:.2f}")
        for name, factor in result["factors"].items():
            print(f"    {name}: {factor['score']:.2f} — {factor.get('signal', '')}")

        # Funding rate detail
        fr = client.get_funding_rate(sym)
        if fr:
            print(f"\n  OI-Weighted FR: {fr['weighted_rate']*100:.4f}%")
            print(f"  Total OI: ${fr['total_oi_usd']/1e9:.2f}B")
            for ex in fr.get("exchanges", [])[:3]:
                print(f"    {ex['exchange']}: {ex['rate']*100:.4f}%")


if __name__ == "__main__":
    main()
