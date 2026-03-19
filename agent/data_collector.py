"""Data Collector — 全自動市場數據收集，純 Python，零 AI token。

每 8 小時由 cron 觸發，收集所有市場數據到 JSON。
數據源全部免費 API，Python 直接呼叫。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("DATA_DIR", "/app/data"))
SNAPSHOT_PATH = DATA_DIR / "market_snapshot.json"


def collect_confidence() -> dict:
    """收集信心引擎分數。"""
    try:
        from market_monitor.confidence_engine import GlobalConfidenceEngine
        engine = GlobalConfidenceEngine()
        result = engine.calculate()
        return {
            "score": result["score"],
            "regime": result["regime"],
            "event_multiplier": result["event_multiplier"],
            "sandboxes": result["sandboxes"],
        }
    except Exception as e:
        logger.warning("Confidence fetch failed: %s", e)
        return {"score": 0.5, "regime": "UNKNOWN", "error": str(e)}


def collect_crypto_env() -> dict:
    """收集 6 幣種加密環境分數。"""
    results = {}
    try:
        from market_monitor.crypto_environment import CryptoEnvironmentEngine
        cg_key = os.environ.get("COINGLASS_API_KEY")
        engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
        for sym in ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE"]:
            try:
                r = engine.calculate(sym)
                results[sym] = {
                    "score": r["score"],
                    "regime": r["regime"],
                    "sandboxes": r["sandboxes"],
                    "signals": [
                        f.get("signal", "")
                        for f in r.get("factors", {}).values()
                        if f.get("signal") and f["signal"] not in ("neutral", "stable", "no data")
                    ],
                }
            except Exception as e:
                results[sym] = {"score": 0.5, "regime": "UNKNOWN", "error": str(e)}
    except Exception as e:
        logger.warning("Crypto env fetch failed: %s", e)
    return results


def collect_macro() -> dict:
    """收集宏觀指標 (批量 yfinance)。"""
    try:
        import yfinance as yf
        tickers = {"VIX": "^VIX", "10Y": "^TNX", "Gold": "GC=F", "Oil": "CL=F", "DXY": "DX-Y.NYB"}
        result = {}
        for name, ticker in tickers.items():
            try:
                df = yf.Ticker(ticker).history(period="5d")
                if len(df) >= 2:
                    price = float(df["Close"].iloc[-1])
                    prev = float(df["Close"].iloc[-2])
                    chg = (price / prev - 1) * 100
                    result[name] = {"price": round(price, 2), "change_pct": round(chg, 2)}
            except Exception:
                pass

        # Fear & Greed
        try:
            import urllib.request
            url = "https://api.alternative.me/fng/?limit=1"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("data"):
                    result["fear_greed"] = int(data["data"][0]["value"])
        except Exception:
            pass

        # BTC Dominance
        try:
            import urllib.request
            url = "https://api.coingecko.com/api/v3/global"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                result["btc_dominance"] = round(data["data"]["market_cap_percentage"]["btc"], 1)
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning("Macro fetch failed: %s", e)
        return {}


def detect_regime() -> dict:
    """偵測市場機制 (純規則)。"""
    try:
        from agent.regime_detector import RegimeDetector
        return RegimeDetector().detect()
    except Exception as e:
        return {"regime": "UNKNOWN", "confidence": 0, "error": str(e)}


def get_freqtrade_status() -> dict:
    """取得 Freqtrade 持倉和績效。"""
    import base64
    import urllib.request

    result = {"positions": [], "profit": {}}
    hosts = ["freqtrade:8080", "localhost:8080"]

    for host in hosts:
        try:
            auth = base64.b64encode(b"freqtrade:freqtrade").decode()
            headers = {"Authorization": f"Basic {auth}"}

            # Positions
            req = urllib.request.Request(f"http://{host}/api/v1/status", headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                positions = json.loads(resp.read())
                result["positions"] = positions if isinstance(positions, list) else []

            # Profit
            req2 = urllib.request.Request(f"http://{host}/api/v1/profit", headers=headers)
            with urllib.request.urlopen(req2, timeout=5) as resp2:
                result["profit"] = json.loads(resp2.read())

            return result
        except Exception:
            continue

    return result


def get_guard_state() -> dict:
    """取得 Guard Pipeline 狀態。"""
    try:
        from guards.pipeline import create_default_pipeline, get_guard
        from guards.guards import DailyLossGuard, ConsecutiveLossGuard, CooldownGuard

        daily = get_guard(DailyLossGuard)
        consec = get_guard(ConsecutiveLossGuard)

        return {
            "daily_loss": daily._daily_loss if daily else 0,
            "consecutive_losses": consec._streak if consec else 0,
            "paused_until": consec._paused_until if consec else 0,
        }
    except Exception:
        return {}


def get_recent_decisions(limit: int = 5) -> list[dict]:
    """取得最近的 Agent 決策。"""
    try:
        from agent.memory import AgentMemory
        memory = AgentMemory()
        decisions = memory.get_decisions(limit=limit)
        return [
            {
                "id": d["id"],
                "time": datetime.fromtimestamp(d["timestamp"]).strftime("%H:%M"),
                "action": d["action"],
                "confidence": d["confidence"],
                "regime": d.get("regime"),
            }
            for d in decisions
        ]
    except Exception:
        return []


def collect_all() -> dict:
    """收集所有市場數據到 JSON。"""
    logger.info("=== Data Collection Started ===")
    start = time.time()

    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "confidence": collect_confidence(),
        "crypto_env": collect_crypto_env(),
        "regime": detect_regime(),
        "freqtrade": get_freqtrade_status(),
        "macro": collect_macro(),
        "guards": get_guard_state(),
        "recent_decisions": get_recent_decisions(5),
    }

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False, default=str)

    elapsed = time.time() - start
    logger.info("Data collection complete in %.1fs → %s", elapsed, SNAPSHOT_PATH)
    return snapshot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    result = collect_all()
    print(json.dumps({"status": "ok", "keys": list(result.keys()), "path": str(SNAPSHOT_PATH)}, indent=2))
