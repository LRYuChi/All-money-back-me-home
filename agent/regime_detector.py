"""Market Regime Detector — pure rule-based, no LLM dependency.

Deterministic regime classification based on market indicators.
Used by the Agent Brain for context-aware decisions.

Regimes:
  TRENDING_BULL:   Structural uptrend, EMA aligned
  TRENDING_BEAR:   Structural downtrend, EMA aligned
  HIGH_VOLATILITY: ATR spike, extreme moves
  ACCUMULATION:    Low volatility compression
  RANGING:         No clear direction
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Pure rule-based market regime detector."""

    def detect(self) -> dict[str, Any]:
        """Detect current market regime from available data.

        Returns:
            regime: str — one of TRENDING_BULL/BEAR, HIGH_VOLATILITY, ACCUMULATION, RANGING
            confidence: float — how confident we are in the classification (0-1)
            factors: dict — evidence for the classification
        """
        data = self._fetch_dashboard()
        if not data:
            return {"regime": "UNKNOWN", "confidence": 0, "factors": {}, "error": "no data"}

        factors = {}

        # 1. Confidence engine regime
        conf_score = data.get("confidence", {}).get("score", 0.5)
        conf_regime = data.get("confidence", {}).get("regime", "CAUTIOUS")
        factors["confidence"] = {"score": conf_score, "regime": conf_regime}

        # 2. BTC price momentum
        # Handle both dict and list formats
        crypto_data = data.get("crypto", data.get("crypto_env", {}))
        if isinstance(crypto_data, dict):
            btc = crypto_data.get("BTC", {})
        elif isinstance(crypto_data, list):
            btc = next((c for c in crypto_data if c.get("name") == "BTC"), {})
        else:
            btc = {}
        btc_change = btc.get("change_pct", 0)
        btc_rsi = btc.get("rsi", 50)
        factors["btc"] = {"change_pct": btc_change, "rsi": btc_rsi}

        # 3. VIX
        vix = data.get("macro", {}).get("vix", {}).get("price", 20)
        factors["vix"] = vix

        # 4. Fear & Greed
        fg_raw = data.get("macro", {}).get("fear_greed", 50)
        if isinstance(fg_raw, dict):
            fg = fg_raw.get("value", 50)
        else:
            fg = fg_raw if isinstance(fg_raw, (int, float)) else 50
        factors["fear_greed"] = fg

        # 5. Crypto Environment
        crypto_env = data.get("crypto_env", {})
        btc_env = crypto_env.get("BTC", {}).get("score", 0.5) if crypto_env else 0.5
        factors["btc_env"] = btc_env

        # 6. BTC Dominance
        btc_d = data.get("macro", {}).get("btc_dominance", 50)
        factors["btc_dominance"] = btc_d

        # === Classification Logic ===
        regime, confidence = self._classify(conf_score, conf_regime, btc_change, btc_rsi, vix, fg, btc_env)

        logger.info(
            "Regime: %s (conf=%.2f) | factors: conf_score=%.2f btc_change=%.1f%% vix=%.1f fg=%d btc_env=%.2f",
            regime, confidence, conf_score, btc_change * 100 if btc_change else 0,
            vix, fg, btc_env
        )

        return {
            "regime": regime,
            "confidence": confidence,
            "factors": factors,
            "guidance": self._regime_guidance(regime),
        }

    def _classify(self, conf_score, conf_regime, btc_change, btc_rsi, vix, fg, btc_env) -> tuple[str, float]:
        """Rule-based classification."""

        # HIGH_VOLATILITY: VIX > 30 or BTC 24h move > 5%
        if vix > 30 or abs(btc_change) > 5:
            return "HIGH_VOLATILITY", 0.85

        # TRENDING_BEAR: Low confidence + bearish signals
        if conf_score < 0.25 and fg < 25:
            return "TRENDING_BEAR", 0.80

        # TRENDING_BULL: High confidence + bullish signals
        if conf_score >= 0.65 and btc_env >= 0.6 and fg > 50:
            return "TRENDING_BULL", 0.80

        # ACCUMULATION: Low VIX + moderate confidence + fear
        if vix < 18 and 0.3 <= conf_score <= 0.6 and fg < 40:
            return "ACCUMULATION", 0.65

        # RANGING: Default when no clear signal
        return "RANGING", 0.50

    @staticmethod
    def _regime_guidance(regime: str) -> dict:
        """Trading guidance for each regime."""
        return {
            "TRENDING_BULL": {
                "strategy": "trend_follow",
                "leverage_cap": 3.0,
                "risk_level": "aggressive",
                "description": "結構性上升趨勢，跟隨趨勢操作",
            },
            "TRENDING_BEAR": {
                "strategy": "short_trend",
                "leverage_cap": 2.0,
                "risk_level": "conservative",
                "description": "結構性下降趨勢，反轉做空或觀望",
            },
            "HIGH_VOLATILITY": {
                "strategy": "cash_first",
                "leverage_cap": 1.5,
                "risk_level": "conservative",
                "description": "極端波動，減少曝險，現金為王",
            },
            "ACCUMULATION": {
                "strategy": "breakout_watch",
                "leverage_cap": 2.0,
                "risk_level": "normal",
                "description": "低波動蓄勢，關注突破信號",
            },
            "RANGING": {
                "strategy": "scalp_or_wait",
                "leverage_cap": 2.0,
                "risk_level": "normal",
                "description": "無明確方向，小倉位或等待",
            },
            "UNKNOWN": {
                "strategy": "wait",
                "leverage_cap": 1.0,
                "risk_level": "conservative",
                "description": "數據不足，等待",
            },
        }.get(regime, {"strategy": "wait", "leverage_cap": 1.0, "risk_level": "conservative", "description": "未知"})

    def _fetch_dashboard(self) -> dict | None:
        """Fetch dashboard data from API."""
        try:
            urls = [
                "http://api:8000/api/dashboard",      # Docker internal
                "http://localhost:8000/api/dashboard",  # Local
                "http://localhost/api/dashboard",       # Via nginx
            ]
            for url in urls:
                try:
                    with urllib.request.urlopen(url, timeout=15) as resp:
                        return json.loads(resp.read())
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Dashboard fetch failed: %s", e)
        return None


# CLI
def main():
    detector = RegimeDetector()
    result = detector.detect()
    print(f"\n市場機制: {result['regime']} (信心: {result['confidence']:.0%})")
    print(f"建議: {result.get('guidance', {}).get('description', '')}")
    print(f"槓桿上限: {result.get('guidance', {}).get('leverage_cap', 1.0)}x")
    print("\n因子:")
    for k, v in result.get("factors", {}).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
