"""Data Freshness & Health Check — 資料源健康偵測模組。

檢測各資料源的可用性與資料新鮮度，提供健康狀態報告。
所有檢查使用 urllib.request，無額外依賴。

使用方式:
    from market_monitor.health_check import DataFreshnessChecker
    checker = DataFreshnessChecker()
    health = checker.check_api_health()
    report = checker.get_health_report()
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# 各資料源的 API 端點
_ENDPOINTS: dict[str, str] = {
    "binance_funding": "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1",
    "binance_ls": "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=4h&limit=1",
    "bybit_oi": "https://api.bybit.com/v5/market/open-interest?category=linear&symbol=BTCUSDT",
    "cfgi_fg": "https://api.alternative.me/fng/?limit=1",
    "mempool": "https://mempool.space/api/v1/fees/recommended",
    "defillama": "https://api.llama.fi/v2/historicalChainTvl",
    "coingecko": "https://api.coingecko.com/api/v3/global",
}

# 時間框架對應秒數（用於 K 線新鮮度判斷）
_TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}

_REQUEST_TIMEOUT = 5  # 秒


def _ping_url(url: str) -> tuple[bool, str]:
    """發送輕量 HTTP 請求測試端點可用性。

    回傳 (成功與否, 錯誤訊息或空字串)。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            # 只讀取少量資料確認有回應
            resp.read(4096)
            return True, ""
    except Exception as e:
        return False, str(e)


class DataFreshnessChecker:
    """資料源健康與新鮮度檢測器。"""

    def check_candle_freshness(
        self,
        pair: str,
        latest_candle_time: datetime,
        timeframe: str,
    ) -> bool:
        """檢查 K 線資料是否仍然新鮮。

        判斷標準：最新 K 線時間距離現在不超過 2 倍的時間框架週期。

        Args:
            pair: 交易對名稱 (如 "BTCUSDT")
            latest_candle_time: 最新 K 線的時間戳（應為 UTC）
            timeframe: 時間框架 (如 "1h", "4h", "1d")

        Returns:
            True 表示資料新鮮，False 表示資料過期
        """
        period_sec = _TIMEFRAME_SECONDS.get(timeframe)
        if period_sec is None:
            logger.warning("未知時間框架: %s，無法判斷新鮮度", timeframe)
            return True  # 未知框架不阻擋

        now = datetime.now(timezone.utc)
        # 確保 latest_candle_time 有時區資訊
        if latest_candle_time.tzinfo is None:
            latest_candle_time = latest_candle_time.replace(tzinfo=timezone.utc)

        elapsed = (now - latest_candle_time).total_seconds()
        threshold = period_sec * 2

        is_fresh = elapsed <= threshold
        if not is_fresh:
            logger.warning(
                "K 線資料過期: %s [%s] 最新時間 %s，已逾 %.0f 秒（閾值 %d 秒）",
                pair,
                timeframe,
                latest_candle_time.isoformat(),
                elapsed,
                threshold,
            )
        return is_fresh

    def check_api_health(self) -> dict[str, bool]:
        """逐一測試各資料源端點，回傳可用性字典。

        Returns:
            {source_name: bool} — True 表示端點可用
        """
        results: dict[str, bool] = {}

        # HTTP 端點檢查
        for source, url in _ENDPOINTS.items():
            ok, err = _ping_url(url)
            results[source] = ok
            if not ok:
                logger.warning("資料源 %s 不可用: %s", source, err)
            else:
                logger.debug("資料源 %s 正常", source)

        # yfinance 檢查：嘗試 import 並快速取得 ^VIX
        results["yfinance"] = self._check_yfinance()

        # FRED 檢查：確認環境變數已設定
        results["fred"] = self._check_fred()

        return results

    @staticmethod
    def _check_yfinance() -> bool:
        """檢查 yfinance 是否可用。嘗試取得 ^VIX 最新一筆資料。"""
        try:
            import yfinance as yf
            df = yf.Ticker("^VIX").history(period="1d")
            if df is not None and not df.empty:
                logger.debug("yfinance 正常：^VIX 最新收盤 %.2f", df["Close"].iloc[-1])
                return True
            logger.warning("yfinance 回傳空資料")
            return False
        except ImportError:
            logger.warning("yfinance 未安裝")
            return False
        except Exception as e:
            logger.warning("yfinance 檢查失敗: %s", e)
            return False

    @staticmethod
    def _check_fred() -> bool:
        """檢查 FRED API Key 環境變數是否已設定。"""
        key = os.environ.get("FRED_API_KEY", "")
        if key:
            logger.debug("FRED API Key 已設定")
            return True
        logger.warning("FRED_API_KEY 環境變數未設定")
        return False

    def get_health_report(self) -> dict[str, Any]:
        """產生完整健康狀態報告。

        Returns:
            包含各資料源狀態、摘要、時間戳的字典
        """
        health = self.check_api_health()
        total = len(health)
        healthy = sum(1 for v in health.values() if v)
        unhealthy_sources = [k for k, v in health.items() if not v]

        report: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": health,
            "summary": {
                "total": total,
                "healthy": healthy,
                "unhealthy": total - healthy,
                "health_pct": round(healthy / max(total, 1) * 100, 1),
            },
            "unhealthy_sources": unhealthy_sources,
        }

        # 嚴重性判斷
        health_pct = report["summary"]["health_pct"]
        if health_pct >= 80:
            report["severity"] = "OK"
        elif health_pct >= 50:
            report["severity"] = "WARNING"
        else:
            report["severity"] = "CRITICAL"

        if unhealthy_sources:
            logger.warning(
                "健康檢查完成：%d/%d 資料源正常，不可用: %s",
                healthy,
                total,
                ", ".join(unhealthy_sources),
            )
        else:
            logger.info("健康檢查完成：所有 %d 個資料源皆正常", total)

        return report


# CLI 測試
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    checker = DataFreshnessChecker()

    print("=== 資料源健康檢查 ===\n")
    report = checker.get_health_report()

    print(f"時間: {report['timestamp']}")
    print(f"嚴重性: {report['severity']}")
    print(
        f"摘要: {report['summary']['healthy']}/{report['summary']['total']} "
        f"正常 ({report['summary']['health_pct']}%)"
    )
    print("\n各資料源狀態:")
    for source, ok in report["sources"].items():
        status = "✓ 正常" if ok else "✗ 異常"
        print(f"  {source:>20}: {status}")

    if report["unhealthy_sources"]:
        print(f"\n不可用資料源: {', '.join(report['unhealthy_sources'])}")

    # 測試 K 線新鮮度
    print("\n=== K 線新鮮度測試 ===")
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    fresh_time = now - timedelta(hours=1)
    stale_time = now - timedelta(hours=10)

    print(f"  1h 內的 4h K 線: {checker.check_candle_freshness('BTCUSDT', fresh_time, '4h')}")
    print(f"  10h 前的 4h K 線: {checker.check_candle_freshness('BTCUSDT', stale_time, '4h')}")
