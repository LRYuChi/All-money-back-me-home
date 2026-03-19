"""Crypto Environment Engine — crypto-specific market state assessment.

Separate from the Global Confidence Engine (macro/economic factors),
this engine evaluates crypto market internal conditions using FREE
public APIs (no API keys required).

Three sandboxes:
1. Derivatives (40%): Funding Rate, Long/Short Ratio, Open Interest
2. On-chain (30%): Mempool activity, DeFi TVL momentum
3. Sentiment (30%): Per-token Fear&Greed, news sentiment

Output: crypto_score 0.0-1.0
  > 0.7 = crypto environment favors trading (momentum, conviction)
  < 0.3 = crypto environment hostile (crowded, overleveraged)
  0.3-0.7 = neutral

Usage:
    from market_monitor.crypto_environment import CryptoEnvironmentEngine
    engine = CryptoEnvironmentEngine()
    result = engine.calculate("BTC")
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Cache to avoid hitting rate limits
_cache: dict[str, Any] = {}
_cache_ts: dict[str, float] = {}
CACHE_TTL = 300  # 5 minutes


def _fetch(url: str, cache_key: str | None = None) -> Any:
    """Fetch JSON with caching."""
    if cache_key and cache_key in _cache:
        if time.time() - _cache_ts.get(cache_key, 0) < CACHE_TTL:
            return _cache[cache_key]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if cache_key:
                _cache[cache_key] = data
                _cache_ts[cache_key] = time.time()
            return data
    except Exception as e:
        logger.warning("Crypto env fetch failed (%s): %s", url[:60], e)
        return None


class CryptoEnvironmentEngine:
    """Evaluates crypto market internal conditions."""

    WEIGHTS = {
        "derivatives": 0.40,
        "onchain": 0.30,
        "sentiment": 0.30,
    }

    def __init__(self, coinglass_api_key: str | None = None):
        self._cg_client = None
        if coinglass_api_key:
            try:
                from market_monitor.coinglass import CoinGlassClient
                self._cg_client = CoinGlassClient(coinglass_api_key)
                logger.info("CoinGlass API enabled for enhanced derivatives data")
            except Exception as e:
                logger.warning("CoinGlass init failed: %s", e)

    def calculate(self, symbol: str = "BTC") -> dict:
        """Calculate crypto environment score for a symbol."""
        # Use CoinGlass for derivatives if available, else fallback to free APIs
        if self._cg_client:
            deriv = self._derivatives_sandbox_coinglass(symbol)
        else:
            deriv = self._derivatives_sandbox(symbol)
        onchain = self._onchain_sandbox()
        sentiment = self._sentiment_sandbox(symbol)

        raw_score = (
            self.WEIGHTS["derivatives"] * deriv["score"]
            + self.WEIGHTS["onchain"] * onchain["score"]
            + self.WEIGHTS["sentiment"] * sentiment["score"]
        )
        score = round(float(np.clip(raw_score, 0, 1)), 4)

        if score >= 0.7:
            regime = "FAVORABLE"
        elif score >= 0.5:
            regime = "NEUTRAL"
        elif score >= 0.3:
            regime = "CAUTIOUS"
        else:
            regime = "HOSTILE"

        return {
            "score": score,
            "regime": regime,
            "symbol": symbol,
            "sandboxes": {
                "derivatives": round(deriv["score"], 4),
                "onchain": round(onchain["score"], 4),
                "sentiment": round(sentiment["score"], 4),
            },
            "factors": {
                **deriv.get("factors", {}),
                **onchain.get("factors", {}),
                **sentiment.get("factors", {}),
            },
        }

    # ==========================================================
    # 1. DERIVATIVES SANDBOX (40%)
    # ==========================================================

    def _derivatives_sandbox(self, symbol: str) -> dict:
        fr = self._funding_rate(symbol)
        ls = self._long_short_ratio(symbol)
        oi = self._open_interest_trend(symbol)

        score = fr["score"] * 0.375 + ls["score"] * 0.375 + oi["score"] * 0.25
        return {
            "score": score,
            "factors": {
                "funding_rate": fr,
                "long_short_ratio": ls,
                "open_interest": oi,
            },
        }

    def _funding_rate(self, symbol: str) -> dict:
        """Binance funding rate — contrarian indicator."""
        data = _fetch(
            f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}USDT&limit=10",
            f"fr_{symbol}",
        )
        if not data:
            return {"score": 0.5, "value": None, "signal": "no data"}

        avg = sum(float(d["fundingRate"]) for d in data) / len(data)
        # Contrarian: negative FR = shorts paying = bullish
        # Extreme positive FR = longs overleveraged = bearish
        score = float(np.clip(0.5 - avg * 500, 0, 1))

        signal = "neutral"
        if avg > 0.0005:
            signal = "overleveraged longs ⚠️"
        elif avg < -0.0003:
            signal = "shorts paying → bullish"
        elif avg < 0:
            signal = "slightly bullish"

        return {"score": round(score, 4), "value": round(avg * 100, 4), "signal": signal}

    def _long_short_ratio(self, symbol: str) -> dict:
        """Binance global long/short account ratio — contrarian."""
        data = _fetch(
            f"https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}USDT&period=4h&limit=10",
            f"ls_{symbol}",
        )
        if not data:
            return {"score": 0.5, "value": None, "signal": "no data"}

        latest = float(data[-1]["longShortRatio"])
        avg = sum(float(d["longShortRatio"]) for d in data) / len(data)

        # Contrarian: high long ratio = crowd long = bearish signal
        deviation = latest / max(avg, 0.01)
        score = float(np.clip(1.0 - deviation / 2.0, 0, 1))

        signal = "neutral"
        if latest > 1.5:
            signal = f"crowd heavily long ({latest:.2f}) ⚠️"
        elif latest < 0.7:
            signal = f"crowd heavily short ({latest:.2f}) → bullish"

        return {"score": round(score, 4), "value": round(latest, 4), "signal": signal}

    def _open_interest_trend(self, symbol: str) -> dict:
        """Open interest trend — Bybit first, Binance fallback."""
        # Try Bybit first
        data = _fetch(
            f"https://api.bybit.com/v5/market/open-interest?category=linear&symbol={symbol}USDT&intervalTime=1h&limit=24",
            f"oi_{symbol}",
        )
        # Fallback to Binance if Bybit fails (403 in some regions)
        if not data or not data.get("result", {}).get("list"):
            binance_data = _fetch(
                f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}USDT&period=1h&limit=24",
                f"oi_binance_{symbol}",
            )
            if binance_data and isinstance(binance_data, list) and len(binance_data) > 1:
                values = [float(d.get("sumOpenInterest", 0)) for d in binance_data]
                change_pct = (values[-1] - values[0]) / max(values[0], 1) * 100
                score = float(np.clip(0.5 + change_pct * 5, 0, 1))
                signal = "stable"
                if change_pct > 3:
                    signal = f"OI rising +{change_pct:.1f}% → conviction"
                elif change_pct < -3:
                    signal = f"OI falling {change_pct:.1f}% → unwinding"
                return {"score": round(score, 4), "value": round(change_pct, 2), "signal": signal, "source": "binance"}
            return {"score": 0.5, "value": None, "signal": "no data"}
        if not data or not data.get("result", {}).get("list"):
            return {"score": 0.5, "value": None, "signal": "no data"}

        oi_list = data["result"]["list"]
        values = [float(d["openInterest"]) for d in oi_list]
        values.reverse()

        if len(values) < 2:
            return {"score": 0.5, "value": None, "signal": "insufficient data"}

        change_pct = (values[-1] - values[0]) / max(values[0], 1) * 100

        # Rising OI = new capital = conviction. Map ±10% to 0-1
        score = float(np.clip(0.5 + change_pct * 5, 0, 1))

        signal = "stable"
        if change_pct > 3:
            signal = f"OI rising +{change_pct:.1f}% → conviction"
        elif change_pct < -3:
            signal = f"OI falling {change_pct:.1f}% → unwinding"

        return {"score": round(score, 4), "value": round(change_pct, 2), "signal": signal}

    # ==========================================================
    # 2. ON-CHAIN SANDBOX (30%)
    # ==========================================================

    def _onchain_sandbox(self) -> dict:
        mempool = self._mempool_activity()
        tvl = self._defi_tvl_momentum()

        score = mempool["score"] * 0.5 + tvl["score"] * 0.5
        return {
            "score": score,
            "factors": {
                "mempool": mempool,
                "defi_tvl": tvl,
            },
        }

    def _mempool_activity(self) -> dict:
        """Mempool.space — BTC network activity."""
        data = _fetch("https://mempool.space/api/mempool", "mempool")
        fees = _fetch("https://mempool.space/api/v1/fees/recommended", "fees")

        if not data or not fees:
            return {"score": 0.5, "value": None, "signal": "no data"}

        tx_count = data.get("count", 0)
        fastest_fee = fees.get("fastestFee", 10)

        # High activity = volatile market ahead
        # Moderate fees (10-50 sat/vB) = healthy. Extreme (>100) = congestion
        if fastest_fee > 100:
            score = 0.3  # Extreme congestion = caution
            signal = f"congestion ({fastest_fee} sat/vB) ⚠️"
        elif fastest_fee > 50:
            score = 0.6  # Active
            signal = f"active ({fastest_fee} sat/vB)"
        else:
            score = 0.7  # Normal
            signal = f"normal ({fastest_fee} sat/vB)"

        return {"score": score, "value": fastest_fee, "signal": signal, "tx_count": tx_count}

    def _defi_tvl_momentum(self) -> dict:
        """DefiLlama — DeFi TVL momentum (7-day)."""
        data = _fetch("https://api.llama.fi/v2/historicalChainTvl", "tvl")
        if not data or len(data) < 8:
            return {"score": 0.5, "value": None, "signal": "no data"}

        recent_7d = data[-7:]
        tvl_start = recent_7d[0].get("tvl", 0)
        tvl_end = recent_7d[-1].get("tvl", 0)

        if tvl_start <= 0:
            return {"score": 0.5, "value": None, "signal": "bad data"}

        change_pct = (tvl_end - tvl_start) / tvl_start * 100
        # Rising TVL = capital inflow = bullish
        score = float(np.clip(0.5 + change_pct * 3, 0, 1))

        signal = "stable"
        if change_pct > 2:
            signal = f"TVL rising +{change_pct:.1f}% → inflow"
        elif change_pct < -2:
            signal = f"TVL falling {change_pct:.1f}% → outflow"

        return {"score": round(score, 4), "value": round(change_pct, 2), "signal": signal}

    # ==========================================================
    # 3. SENTIMENT SANDBOX (30%)
    # ==========================================================

    def _sentiment_sandbox(self, symbol: str) -> dict:
        fg = self._fear_greed(symbol)
        # News sentiment requires API key, use F&G as proxy
        score = fg["score"]
        return {
            "score": score,
            "factors": {
                "fear_greed_token": fg,
            },
        }

    def _fear_greed(self, symbol: str) -> dict:
        """CFGI.io per-token Fear & Greed (or fallback to Alternative.me)."""
        # Try CFGI.io first (per-token)
        data = _fetch(f"https://cfgi.io/api/fear-greed/{symbol}", f"fg_{symbol}")
        if data and isinstance(data, dict) and "value" in data:
            val = int(data["value"])
        else:
            # Fallback: Alternative.me (BTC only)
            data = _fetch("https://api.alternative.me/fng/?limit=1", "fg_alt")
            if data and data.get("data"):
                val = int(data["data"][0]["value"])
            else:
                return {"score": 0.5, "value": None, "signal": "no data"}

        # Contrarian at extremes, confirming in middle
        if val <= 20:
            score = 0.75  # Extreme fear = contrarian buy
            signal = f"extreme fear ({val}) → buy signal"
        elif val <= 35:
            score = 0.6
            signal = f"fear ({val})"
        elif val <= 65:
            score = 0.5
            signal = f"neutral ({val})"
        elif val <= 80:
            score = 0.4
            signal = f"greed ({val})"
        else:
            score = 0.25  # Extreme greed = contrarian sell
            signal = f"extreme greed ({val}) → caution"

        return {"score": score, "value": val, "signal": signal}

    # ==========================================================
    # ENHANCED: CoinGlass Derivatives Sandbox
    # ==========================================================

    def _derivatives_sandbox_coinglass(self, symbol: str) -> dict:
        """Enhanced derivatives sandbox using CoinGlass API.

        Replaces basic Binance-only data with OI-weighted cross-exchange data.
        Falls back to free API sandbox if CoinGlass call fails.
        """
        try:
            result = self._cg_client.calculate_derivatives_score(symbol)
            if result and result.get("score") is not None:
                logger.info("CoinGlass derivatives for %s: %.2f", symbol, result["score"])
                return {
                    "score": result["score"],
                    "factors": result["factors"],
                }
        except Exception as e:
            logger.warning("CoinGlass derivatives failed for %s: %s, falling back", symbol, e)

        # Fallback to free APIs
        return self._derivatives_sandbox(symbol)


# CLI
def main():
    import os
    cg_key = os.environ.get("COINGLASS_API_KEY")
    engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
    for sym in ["BTC", "ETH", "SOL"]:
        result = engine.calculate(sym)
        print(f"\n{sym}: {result['score']:.2f} ({result['regime']})")
        for sandbox, val in result["sandboxes"].items():
            print(f"  {sandbox}: {val:.2f}")
        for name, factor in result["factors"].items():
            print(f"    {name}: {factor.get('score', '?'):.2f} — {factor.get('signal', '')}")


if __name__ == "__main__":
    main()
