"""Global Confidence Engine — Institutional-Grade Market Regime Assessment.

Four sandbox architecture:
1. Macro Sandbox (35%): NFCI, M2, 10Y yield, DXY, GPR proxy, Oil trend
2. Sentiment Sandbox (30%): VIX, Fear&Greed, Funding Rate, GS RAI proxy
3. Capital Flow Sandbox (20%): BTC.D, Stablecoin supply, OI, SPY-BTC correlation
4. Haven/Inflation Sandbox (15%): Gold trend, Gold/Oil ratio, Gold-BTC correlation

Each factor is Z-Score normalized independently.
Sandboxes are isolated then weighted-combined.
Event calendar applies multiplicative overlays.
Output: confidence score 0.0-1.0 with EMA smoothing.

Data sources: FRED, yfinance, alternative.me, CoinGecko, DefiLlama — all FREE.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# =============================================
# Data Fetchers
# =============================================

def fetch_yfinance_data(ticker: str, period: str = "6mo") -> pd.Series:
    """Fetch close prices from yfinance."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period)
        if not df.empty:
            return df["Close"]
    except Exception as e:
        logger.warning("yfinance fetch failed for %s: %s", ticker, e)
    return pd.Series(dtype=float)


def fetch_fred_series(series_id: str, days: int = 365) -> pd.Series:
    """Fetch data from FRED (requires FRED_API_KEY in env or fallback to yfinance)."""
    try:
        # Try fredapi first
        import os
        api_key = os.environ.get("FRED_API_KEY")
        if api_key:
            from fredapi import Fred
            fred = Fred(api_key=api_key)
            start = datetime.now() - timedelta(days=days)
            return fred.get_series(series_id, observation_start=start)
    except Exception:
        pass

    # Fallback: some FRED series available via yfinance
    fred_to_yf = {
        "VIXCLS": "^VIX",
        "DGS10": "^TNX",
    }
    if series_id in fred_to_yf:
        return fetch_yfinance_data(fred_to_yf[series_id])

    logger.warning("FRED series %s unavailable", series_id)
    return pd.Series(dtype=float)


def fetch_fear_greed() -> float:
    """Fetch Crypto Fear & Greed Index (0-100) from alternative.me."""
    try:
        import urllib.request
        url = "https://api.alternative.me/fng/?limit=1"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            return float(data["data"][0]["value"])
    except Exception as e:
        logger.warning("Fear & Greed fetch failed: %s", e)
        return 50.0  # Neutral default


_btc_d_cache: dict = {"value": 50.0, "ts": 0}

def fetch_btc_dominance() -> float:
    """Fetch BTC dominance % from CoinGecko (cached 30 min to avoid 429)."""
    import time
    now = time.time()
    if now - _btc_d_cache["ts"] < 1800:  # 30 min cache
        return _btc_d_cache["value"]
    try:
        import urllib.request
        url = "https://api.coingecko.com/api/v3/global"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            val = float(data["data"]["market_cap_percentage"]["btc"])
            _btc_d_cache["value"] = val
            _btc_d_cache["ts"] = now
            return val
    except Exception as e:
        logger.warning("BTC.D fetch failed: %s", e)
        return _btc_d_cache["value"]


def fetch_stablecoin_mcap() -> float:
    """Fetch total stablecoin market cap from DefiLlama (billions USD)."""
    try:
        import urllib.request
        url = "https://stablecoins.llama.fi/stablecoins?includePrices=false"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            total = sum(
                float(s.get("circulating", {}).get("peggedUSD", 0) or 0)
                for s in data.get("peggedAssets", [])
            )
            return total / 1e9  # In billions
    except Exception as e:
        logger.warning("Stablecoin mcap fetch failed: %s", e)
        return 150.0  # Approximate default


# =============================================
# Z-Score Normalization
# =============================================

def z_score(series: pd.Series, current_value: float, lookback: int = 120) -> float:
    """Calculate Z-Score of current value vs recent history.

    Returns: Z-Score (0 = average, +2 = 2 std above, -2 = 2 std below)
    """
    if len(series) < 10:
        return 0.0
    recent = series.tail(lookback).dropna()
    if len(recent) < 10:
        return 0.0
    mu = recent.mean()
    sigma = recent.std()
    if sigma < 1e-10:
        return 0.0
    return (current_value - mu) / sigma


def z_to_score(z: float, bullish_direction: str = "negative") -> float:
    """Convert Z-Score to 0-1 confidence contribution.

    Args:
        z: Z-Score value
        bullish_direction: "negative" means lower values are bullish (e.g., VIX)
                          "positive" means higher values are bullish (e.g., M2 growth)
    Returns:
        0.0 (very bearish) to 1.0 (very bullish)
    """
    if bullish_direction == "negative":
        # Lower is better (VIX, DXY, yields): z=-2 → 1.0, z=+2 → 0.0
        return float(np.clip(0.5 - z * 0.25, 0.0, 1.0))
    else:
        # Higher is better (M2, stablecoins): z=+2 → 1.0, z=-2 → 0.0
        return float(np.clip(0.5 + z * 0.25, 0.0, 1.0))


# =============================================
# Sandbox Calculators
# =============================================

class MacroSandbox:
    """Macro Economy Sandbox (35%): NFCI, M2, 10Y, DXY, Oil."""

    def calculate(self) -> dict[str, float]:
        scores = {}

        # NFCI (lower = looser = bullish)
        nfci = fetch_fred_series("NFCI")
        if len(nfci) > 0:
            scores["nfci"] = z_to_score(z_score(nfci, nfci.iloc[-1]), "negative")
        else:
            scores["nfci"] = 0.5

        # 10Y Treasury Yield (rising = tighter = bearish for risk)
        tnx = fetch_yfinance_data("^TNX")
        if len(tnx) > 0:
            scores["yield_10y"] = z_to_score(z_score(tnx, tnx.iloc[-1]), "negative")
        else:
            scores["yield_10y"] = 0.5

        # DXY (rising dollar = bearish for risk assets)
        dxy = fetch_yfinance_data("DX-Y.NYB")
        if len(dxy) > 0:
            scores["dxy"] = z_to_score(z_score(dxy, dxy.iloc[-1]), "negative")
        else:
            scores["dxy"] = 0.5

        # M2 Money Supply growth (rising = more liquidity = bullish)
        m2 = fetch_fred_series("WM2NS")
        if len(m2) > 10:
            m2_growth = m2.pct_change(periods=12).dropna()  # YoY growth
            if len(m2_growth) > 0:
                scores["m2_growth"] = z_to_score(z_score(m2_growth, m2_growth.iloc[-1]), "positive")
            else:
                scores["m2_growth"] = 0.5
        else:
            scores["m2_growth"] = 0.5

        # Oil price trend (extreme high = inflation; extreme low = recession; both bad)
        oil = fetch_yfinance_data("CL=F")
        if len(oil) > 0:
            oil_z = z_score(oil, oil.iloc[-1])
            # Stable oil (z near 0) is best; extremes are bad
            scores["oil"] = float(np.clip(1.0 - abs(oil_z) * 0.3, 0.0, 1.0))
        else:
            scores["oil"] = 0.5

        return scores


class SentimentSandbox:
    """Market Sentiment Sandbox (30%): VIX, Fear&Greed, Funding, GS RAI proxy."""

    def calculate(self) -> dict[str, float]:
        scores = {}

        # VIX (lower = calm = bullish)
        vix = fetch_yfinance_data("^VIX")
        if len(vix) > 0:
            scores["vix"] = z_to_score(z_score(vix, vix.iloc[-1]), "negative")
        else:
            scores["vix"] = 0.5

        # Crypto Fear & Greed (extreme fear = contrarian bullish; extreme greed = warning)
        fg = fetch_fear_greed()
        # Map: 0-25 = extreme fear → 0.7 (contrarian buy)
        #       25-50 = fear → 0.6
        #       50-75 = greed → 0.4
        #       75-100 = extreme greed → 0.2 (warning)
        if fg < 25:
            scores["fear_greed"] = 0.7
        elif fg < 50:
            scores["fear_greed"] = 0.6
        elif fg < 75:
            scores["fear_greed"] = 0.4
        else:
            scores["fear_greed"] = 0.2

        # GS Risk Appetite Proxy: SPY/IEF ratio (rising = risk-on)
        spy = fetch_yfinance_data("SPY")
        ief = fetch_yfinance_data("IEF")
        if len(spy) > 0 and len(ief) > 0:
            # Align dates
            combined = pd.DataFrame({"spy": spy, "ief": ief}).dropna()
            if len(combined) > 20:
                ratio = combined["spy"] / combined["ief"]
                scores["gs_rai"] = z_to_score(z_score(ratio, ratio.iloc[-1]), "positive")
            else:
                scores["gs_rai"] = 0.5
        else:
            scores["gs_rai"] = 0.5

        return scores


class CapitalFlowSandbox:
    """Capital Flow Sandbox (20%): BTC.D, Stablecoins, SPY-BTC correlation."""

    def calculate(self) -> dict[str, float]:
        scores = {}

        # BTC Dominance (rising BTC.D = risk-off for alts, neutral-bullish for BTC)
        btc_d = fetch_btc_dominance()
        # Moderate BTC.D (45-55%) is healthy; extreme high or low = stress
        if 45 <= btc_d <= 55:
            scores["btc_d"] = 0.6
        elif btc_d > 65:
            scores["btc_d"] = 0.3  # Too concentrated in BTC
        elif btc_d < 35:
            scores["btc_d"] = 0.3  # Altcoin bubble risk
        else:
            scores["btc_d"] = 0.5

        # Stablecoin market cap growth (more stablecoins = more dry powder = bullish)
        sc_mcap = fetch_stablecoin_mcap()
        # Rough benchmark: >160B = good, >180B = great, <140B = warning
        if sc_mcap > 180:
            scores["stablecoin"] = 0.8
        elif sc_mcap > 160:
            scores["stablecoin"] = 0.6
        elif sc_mcap > 140:
            scores["stablecoin"] = 0.4
        else:
            scores["stablecoin"] = 0.2

        # SPY-BTC rolling correlation (high = crypto follows equities = less independent)
        spy = fetch_yfinance_data("SPY")
        btc = fetch_yfinance_data("BTC-USD")
        if len(spy) > 30 and len(btc) > 30:
            combined = pd.DataFrame({
                "spy": spy.pct_change(),
                "btc": btc.pct_change(),
            }).dropna()
            if len(combined) > 30:
                corr = combined["spy"].rolling(30).corr(combined["btc"]).iloc[-1]
                if not np.isnan(corr):
                    # High positive correlation = crypto follows stocks = less edge
                    # Low/negative = independent movement = more alpha possible
                    scores["spy_btc_corr"] = float(np.clip(0.6 - corr * 0.3, 0.2, 0.8))
                else:
                    scores["spy_btc_corr"] = 0.5
            else:
                scores["spy_btc_corr"] = 0.5
        else:
            scores["spy_btc_corr"] = 0.5

        return scores


class HavenInflationSandbox:
    """Haven/Inflation Sandbox (15%): Gold, Gold/Oil ratio, Gold-BTC correlation."""

    def calculate(self) -> dict[str, float]:
        scores = {}

        gold = fetch_yfinance_data("GC=F")
        oil = fetch_yfinance_data("CL=F")
        btc = fetch_yfinance_data("BTC-USD")

        # Gold trend (rising gold = haven demand = risk-off = bearish for crypto)
        if len(gold) > 20:
            gold_mom = (gold.iloc[-1] / gold.iloc[-20] - 1) * 100  # 20-day momentum %
            # Fast rising gold = risk-off signal
            if gold_mom > 5:
                scores["gold_trend"] = 0.2
            elif gold_mom > 2:
                scores["gold_trend"] = 0.4
            elif gold_mom > -2:
                scores["gold_trend"] = 0.6  # Stable = neutral-positive
            else:
                scores["gold_trend"] = 0.7  # Falling gold = risk-on
        else:
            scores["gold_trend"] = 0.5

        # Gold/Oil ratio (rising = economic slowdown signal)
        if len(gold) > 20 and len(oil) > 20:
            combined = pd.DataFrame({"gold": gold, "oil": oil}).dropna()
            if len(combined) > 20:
                ratio = combined["gold"] / combined["oil"]
                ratio_z = z_score(ratio, ratio.iloc[-1])
                # Extremely high ratio = recession fear; low ratio = overheating
                scores["gold_oil"] = float(np.clip(1.0 - abs(ratio_z) * 0.25, 0.0, 1.0))
            else:
                scores["gold_oil"] = 0.5
        else:
            scores["gold_oil"] = 0.5

        # Gold-BTC correlation (positive = BTC acting as safe haven; negative = risk asset)
        if len(gold) > 30 and len(btc) > 30:
            combined = pd.DataFrame({
                "gold": gold.pct_change(),
                "btc": btc.pct_change(),
            }).dropna()
            if len(combined) > 30:
                corr = combined["gold"].rolling(30).corr(combined["btc"]).iloc[-1]
                if not np.isnan(corr):
                    # Positive gold-BTC corr = BTC as haven = mixed signal
                    # Negative = BTC as risk asset = clearer trading signal
                    scores["gold_btc_corr"] = 0.5  # Neutral — informational
                else:
                    scores["gold_btc_corr"] = 0.5
            else:
                scores["gold_btc_corr"] = 0.5
        else:
            scores["gold_btc_corr"] = 0.5

        return scores


# =============================================
# Event Calendar Overlay
# =============================================

class EventOverlay:
    """Event calendar: multiplicative adjustments to confidence score.

    Known high-impact events reduce confidence temporarily.
    """

    # FOMC meeting dates (2-day meetings, using last day)
    FOMC_DATES = [
        # 2026
        "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
        "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
        # 2027 (projected — same pattern: ~6 weeks apart)
        "2027-01-27", "2027-03-17", "2027-05-05", "2027-06-16",
        "2027-07-28", "2027-09-15", "2027-11-03", "2027-12-15",
    ]

    # CPI release dates (approximate, typically 2nd or 3rd week)
    CPI_DATES = [
        # 2026
        "2026-01-14", "2026-02-11", "2026-03-11", "2026-04-14",
        "2026-05-13", "2026-06-10", "2026-07-14", "2026-08-12",
        "2026-09-15", "2026-10-13", "2026-11-12", "2026-12-10",
        # 2027 (projected)
        "2027-01-13", "2027-02-10", "2027-03-10", "2027-04-13",
        "2027-05-12", "2027-06-10", "2027-07-13", "2027-08-11",
        "2027-09-14", "2027-10-13", "2027-11-10", "2027-12-10",
    ]

    # Quarterly options expiry (3rd Friday of quarter-end month)
    OPTIONS_EXPIRY = [
        "2026-03-20", "2026-06-19", "2026-09-18", "2026-12-18",
        "2027-03-19", "2027-06-18", "2027-09-17", "2027-12-17",
    ]

    def get_multiplier(self, dt: datetime | None = None) -> float:
        """Get event-based confidence multiplier (0.0-1.0)."""
        if dt is None:
            dt = datetime.now()

        date_str = dt.strftime("%Y-%m-%d")
        multiplier = 1.0

        # Check FOMC (±1 day buffer)
        for fomc in self.FOMC_DATES:
            fomc_dt = datetime.strptime(fomc, "%Y-%m-%d")
            if abs((dt - fomc_dt).days) <= 1:
                multiplier = min(multiplier, 0.5)
                break

        # Check CPI (same day)
        if date_str in self.CPI_DATES:
            multiplier = min(multiplier, 0.7)

        # Check options expiry (same day)
        if date_str in self.OPTIONS_EXPIRY:
            multiplier = min(multiplier, 0.8)

        return multiplier


# =============================================
# Main Confidence Engine
# =============================================

class GlobalConfidenceEngine:
    """Combines four sandboxes + event overlay into a single confidence score.

    Weights:
        Macro:      35%
        Sentiment:  30%
        Capital:    20%
        Haven:      15%

    Output: 0.0 (hibernate) to 1.0 (aggressive)
    """

    WEIGHTS = {
        "macro": 0.35,
        "sentiment": 0.30,
        "capital": 0.20,
        "haven": 0.15,
    }

    def __init__(self, ema_span: int = 5):
        self.macro = MacroSandbox()
        self.sentiment = SentimentSandbox()
        self.capital = CapitalFlowSandbox()
        self.haven = HavenInflationSandbox()
        self.events = EventOverlay()
        self.ema_span = ema_span
        self._history: list[float] = []

    def calculate(self, dt: datetime | None = None) -> dict[str, Any]:
        """Calculate the global confidence score.

        Returns dict with:
            score: float 0.0-1.0 (EMA smoothed)
            raw_score: float 0.0-1.0 (before EMA)
            regime: str (AGGRESSIVE/NORMAL/CAUTIOUS/DEFENSIVE/HIBERNATE)
            sandboxes: detailed breakdown
            event_multiplier: float
            factors: all individual factor scores
        """
        # Calculate each sandbox
        macro_scores = self.macro.calculate()
        sentiment_scores = self.sentiment.calculate()
        capital_scores = self.capital.calculate()
        haven_scores = self.haven.calculate()

        # Average within each sandbox
        macro_avg = np.mean(list(macro_scores.values())) if macro_scores else 0.5
        sentiment_avg = np.mean(list(sentiment_scores.values())) if sentiment_scores else 0.5
        capital_avg = np.mean(list(capital_scores.values())) if capital_scores else 0.5
        haven_avg = np.mean(list(haven_scores.values())) if haven_scores else 0.5

        # SAFETY: If most factors returned default 0.5 (data source failure),
        # degrade to DEFENSIVE. Count how many factors are exactly 0.5.
        all_factors = {**macro_scores, **sentiment_scores, **capital_scores, **haven_scores}
        default_count = sum(1 for v in all_factors.values() if v == 0.5)
        total_factors = len(all_factors)
        if total_factors > 0 and default_count >= total_factors * 0.7:
            # 70%+ factors returned defaults = data blackout → force DEFENSIVE
            logger.warning(
                "DATA BLACKOUT: %d/%d factors at default — forcing DEFENSIVE",
                default_count, total_factors
            )
            macro_avg = min(macro_avg, 0.3)
            sentiment_avg = min(sentiment_avg, 0.3)
            capital_avg = min(capital_avg, 0.3)
            haven_avg = min(haven_avg, 0.3)

        # Weighted combination
        raw_score = (
            self.WEIGHTS["macro"] * macro_avg
            + self.WEIGHTS["sentiment"] * sentiment_avg
            + self.WEIGHTS["capital"] * capital_avg
            + self.WEIGHTS["haven"] * haven_avg
        )

        # Event overlay (multiplicative)
        event_mult = self.events.get_multiplier(dt)
        raw_score *= event_mult

        # Clamp
        raw_score = float(np.clip(raw_score, 0.0, 1.0))

        # EMA smoothing
        self._history.append(raw_score)
        if len(self._history) > self.ema_span * 3:
            self._history = self._history[-(self.ema_span * 3):]

        if len(self._history) >= self.ema_span:
            ema_series = pd.Series(self._history).ewm(span=self.ema_span).mean()
            score = float(ema_series.iloc[-1])
        else:
            score = raw_score

        score = float(np.clip(score, 0.0, 1.0))

        # Determine regime
        regime = self._score_to_regime(score)

        return {
            "score": round(score, 4),
            "raw_score": round(raw_score, 4),
            "regime": regime,
            "event_multiplier": event_mult,
            "sandboxes": {
                "macro": round(macro_avg, 4),
                "sentiment": round(sentiment_avg, 4),
                "capital": round(capital_avg, 4),
                "haven": round(haven_avg, 4),
            },
            "factors": {
                "macro": {k: round(v, 4) for k, v in macro_scores.items()},
                "sentiment": {k: round(v, 4) for k, v in sentiment_scores.items()},
                "capital": {k: round(v, 4) for k, v in capital_scores.items()},
                "haven": {k: round(v, 4) for k, v in haven_scores.items()},
            },
            "guidance": self._regime_guidance(regime),
            "timestamp": (dt or datetime.now()).isoformat(),
        }

    @staticmethod
    def _score_to_regime(score: float) -> str:
        if score >= 0.8:
            return "AGGRESSIVE"
        elif score >= 0.6:
            return "NORMAL"
        elif score >= 0.4:
            return "CAUTIOUS"
        elif score >= 0.2:
            return "DEFENSIVE"
        else:
            return "HIBERNATE"

    @staticmethod
    def _regime_guidance(regime: str) -> dict[str, Any]:
        return {
            "AGGRESSIVE": {"position_pct": 100, "leverage": 3.0, "threshold_mult": 1.0},
            "NORMAL":     {"position_pct": 75,  "leverage": 2.0, "threshold_mult": 1.1},
            "CAUTIOUS":   {"position_pct": 50,  "leverage": 1.5, "threshold_mult": 1.25},
            "DEFENSIVE":  {"position_pct": 25,  "leverage": 1.0, "threshold_mult": 1.5},
            "HIBERNATE":  {"position_pct": 0,   "leverage": 0,   "threshold_mult": 999},
        }[regime]


# =============================================
# CLI: Run standalone for dashboard
# =============================================

def main():
    """Run confidence engine and print dashboard."""
    engine = GlobalConfidenceEngine()
    print("Fetching global market data...")
    result = engine.calculate()

    print("\n" + "=" * 60)
    print("GLOBAL CONFIDENCE ENGINE")
    print("=" * 60)

    print(f"\n  Score:   {result['score']:.2f} / 1.00")
    print(f"  Regime:  {result['regime']}")
    print(f"  Event:   ×{result['event_multiplier']:.1f}")

    print("\n  Sandboxes:")
    for name, val in result["sandboxes"].items():
        bar = "█" * int(val * 20) + "░" * (20 - int(val * 20))
        print(f"    {name:>12}: {bar} {val:.2f}")

    print("\n  Factors:")
    for sandbox, factors in result["factors"].items():
        print(f"    [{sandbox}]")
        for k, v in factors.items():
            indicator = "↑" if v > 0.55 else "↓" if v < 0.45 else "→"
            print(f"      {k:>18}: {v:.2f} {indicator}")

    g = result["guidance"]
    print("\n  Guidance:")
    print(f"    Position:  {g['position_pct']}%")
    print(f"    Leverage:  {g['leverage']}x")
    print(f"    Threshold: ×{g['threshold_mult']}")

    print(f"\n  Timestamp: {result['timestamp']}")
    print("=" * 60)

    # Save to file
    out_path = Path(__file__).parent.parent / "data" / "reports" / "confidence.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"\n  Saved to: {out_path}")


if __name__ == "__main__":
    main()
