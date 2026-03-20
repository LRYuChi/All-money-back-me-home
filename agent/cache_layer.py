"""快取層 — 避免相同市場狀態重複呼叫 Claude。"""

import hashlib
import json
import logging
import time

logger = logging.getLogger(__name__)


class AgentCache:

    def __init__(self, ttl_seconds: int = 900):
        self._cache: dict[str, dict] = {}
        self.ttl = ttl_seconds
        self._hits = 0
        self._misses = 0

    def _make_key(self, state: dict) -> str:
        """市場狀態 → hash key（忽略微小差異）"""
        key_data = {
            "regime": state.get("regime"),
            "risk_level": state.get("risk_level") or state.get("agent_risk_level"),
            "consec_loss": state.get("consecutive_losses", 0),
            "btc_rounded": round(state.get("btc_price", 0) / 100) * 100,
            "vol_state": state.get("volatility_state"),
            "funding_extreme": abs(state.get("funding_rate", 0)) > 0.001,
            "confidence_bucket": round(state.get("confidence_score", 0.5) * 5) / 5,
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, state: dict) -> dict | None:
        """查詢快取"""
        key = self._make_key(state)
        entry = self._cache.get(key)
        if not entry:
            self._misses += 1
            return None
        if time.time() - entry["ts"] > self.ttl:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        logger.debug("快取命中 (key=%s)", key[:8])
        return entry["decision"]

    def set(self, state: dict, decision: dict):
        """只快取 no_action 和低信心決策"""
        if decision.get("action") == "no_action" or decision.get("confidence", 1) < 0.6:
            key = self._make_key(state)
            self._cache[key] = {"decision": decision, "ts": time.time()}

    def invalidate_all(self):
        """重大事件時清空快取"""
        self._cache.clear()
        logger.info("快取已全部清空")

    def get_stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": f"{self._hits / total:.0%}" if total > 0 else "N/A",
            "entries": len(self._cache),
        }
