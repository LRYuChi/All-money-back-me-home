"""Bot State Store — JSON 檔案式狀態儲存，支援原子讀寫。

提供交易機器人運行狀態的持久化儲存，使用 JSON 檔案作為後端。
支援多執行緒安全的原子讀寫操作，以及每日計數器自動重置。

使用方式:
    from market_monitor.state_store import BotStateStore
    BotStateStore.increment("signals_generated_today")
    state = BotStateStore.read()
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 資料目錄：優先使用 DATA_DIR 環境變數（Docker 部署），否則使用相對路徑
_data_dir_env = os.environ.get("DATA_DIR")
if _data_dir_env:
    DATA_DIR = Path(_data_dir_env)
else:
    DATA_DIR = Path(__file__).resolve().parent.parent / "data"

STATE_FILE = DATA_DIR / "reports" / "bot_state.json"

# 每日需要重置的計數器欄位
_DAILY_COUNTERS = (
    "guard_rejections_today",
    "signals_generated_today",
    "signals_filtered_today",
    "stale_data_alerts",
)

# 預設狀態結構
_DEFAULT_STATE: dict[str, Any] = {
    "last_updated": None,
    "last_confidence_fetch": None,
    "last_confidence_score": 0.0,
    "last_confidence_regime": "HIBERNATE",
    "guard_rejections_today": 0,
    "circuit_breaker_activations": 0,
    "signals_generated_today": 0,
    "signals_filtered_today": 0,
    "filter_reasons": {},
    "consecutive_wins": 0,
    "consecutive_losses": 0,
    "last_signal_change_time": None,
    "stale_data_alerts": 0,
    "crypto_env_cache": {},
    "api_health": {
        "binance_funding": True,
        "binance_ls": True,
        "bybit_oi": False,
        "cfgi_fg": False,
        "mempool": True,
        "defillama": True,
        "yfinance": True,
        "fred": True,
    },
}


def _now_iso() -> str:
    """回傳 UTC ISO 格式時間戳。"""
    return datetime.now(timezone.utc).isoformat()


def _today_utc() -> str:
    """回傳 UTC 今日日期字串 (YYYY-MM-DD)。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class _FileLock:
    """跨平台檔案鎖定。Linux 使用 fcntl，Windows 使用 msvcrt。"""

    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._fd: Any = None

    def acquire(self) -> None:
        """取得檔案鎖定。"""
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self._lock_path, "w")
        if sys.platform == "win32":
            # Windows: 使用 msvcrt 鎖定
            import msvcrt
            msvcrt.locking(self._fd.fileno(), msvcrt.LK_LOCK, 1)
        else:
            # Linux/macOS: 使用 fcntl 鎖定
            import fcntl
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)

    def release(self) -> None:
        """釋放檔案鎖定。"""
        if self._fd is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
        finally:
            self._fd.close()
            self._fd = None

    def __enter__(self) -> _FileLock:
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


class BotStateStore:
    """JSON 檔案式狀態儲存，支援原子讀寫與每日自動重置。"""

    _lock = _FileLock(STATE_FILE.parent / ".bot_state.lock")
    _cache: dict = {}
    _cache_ts: float = 0
    _CACHE_TTL: int = 30

    @classmethod
    def read(cls) -> dict[str, Any]:
        """讀取目前狀態。若檔案不存在則回傳預設值。"""
        now = time.time()
        if now - cls._cache_ts < cls._CACHE_TTL and cls._cache:
            return cls._cache.copy()

        if not STATE_FILE.exists():
            logger.info("狀態檔案不存在，回傳預設狀態: %s", STATE_FILE)
            return _DEFAULT_STATE.copy()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 合併預設值（處理新增欄位的向下相容）
            merged = _DEFAULT_STATE.copy()
            merged.update(data)
            cls._cache = merged.copy()
            cls._cache_ts = time.time()
            return merged
        except (json.JSONDecodeError, OSError) as e:
            logger.error("讀取狀態檔案失敗: %s，返回安全預設值", e)
            return {
                **_DEFAULT_STATE,
                "agent_pause_entries": False,
                "agent_leverage_cap": 2.0,
                "agent_risk_level": "conservative",
            }

    @classmethod
    def _write(cls, state: dict[str, Any]) -> None:
        """原子寫入狀態至 JSON 檔案。先寫入暫存檔再重新命名，避免中途斷電造成資料毀損。"""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        state["last_updated"] = _now_iso()

        # 寫入暫存檔後以 rename 取代，確保原子性
        fd, tmp_path = tempfile.mkstemp(
            dir=str(STATE_FILE.parent),
            prefix=".bot_state_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            # Windows 不允許 rename 至已存在的檔案，需先刪除
            if sys.platform == "win32" and STATE_FILE.exists():
                STATE_FILE.unlink()
            os.rename(tmp_path, str(STATE_FILE))
            cls._cache = state.copy()
            cls._cache_ts = time.time()
        except Exception:
            # 清理暫存檔
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    @classmethod
    def _check_daily_reset(cls, state: dict[str, Any]) -> dict[str, Any]:
        """檢查是否需要每日重置計數器。依據 last_updated 日期與今日 UTC 日期比較。"""
        last = state.get("last_updated")
        today = _today_utc()
        if last:
            try:
                last_date = last[:10]  # ISO 格式前 10 碼為日期
                if last_date < today:
                    logger.info("跨日偵測：重置每日計數器 (%s → %s)", last_date, today)
                    for key in _DAILY_COUNTERS:
                        state[key] = 0
                    state["filter_reasons"] = {}
            except (ValueError, IndexError):
                pass
        return state

    @classmethod
    def update(cls, **kwargs: Any) -> dict[str, Any]:
        """原子讀取-修改-寫入。傳入的 kwargs 會覆寫對應欄位。

        使用範例:
            BotStateStore.update(
                last_confidence_score=0.72,
                last_confidence_regime="NORMAL",
            )
        """
        with cls._lock:
            state = cls.read()
            state = cls._check_daily_reset(state)
            state.update(kwargs)
            cls._write(state)
            return state.copy()

    @classmethod
    def increment(cls, key: str, amount: int = 1) -> int:
        """原子遞增一個計數器，回傳遞增後的值。

        若 key 對應的值不是整數，會重置為 amount。
        """
        with cls._lock:
            state = cls.read()
            state = cls._check_daily_reset(state)
            current = state.get(key, 0)
            if not isinstance(current, (int, float)):
                current = 0
            new_val = int(current) + amount
            state[key] = new_val
            cls._write(state)
            logger.debug("計數器 %s 遞增 %d → %d", key, amount, new_val)
            return new_val

    @classmethod
    def reset_daily(cls) -> dict[str, Any]:
        """手動重置每日計數器（通常由排程在 UTC 午夜呼叫）。"""
        with cls._lock:
            state = cls.read()
            for key in _DAILY_COUNTERS:
                state[key] = 0
            state["filter_reasons"] = {}
            cls._write(state)
            logger.info("已手動重置每日計數器")
            return state.copy()

    @classmethod
    def record_filter(cls, reason: str) -> None:
        """記錄一次訊號過濾，同時遞增 signals_filtered_today 與 filter_reasons。"""
        with cls._lock:
            state = cls.read()
            state = cls._check_daily_reset(state)
            state["signals_filtered_today"] = state.get("signals_filtered_today", 0) + 1
            reasons = state.get("filter_reasons", {})
            reasons[reason] = reasons.get(reason, 0) + 1
            state["filter_reasons"] = reasons
            cls._write(state)

    @classmethod
    def update_api_health(cls, health: dict[str, bool]) -> None:
        """更新 API 健康狀態。"""
        with cls._lock:
            state = cls.read()
            api_health = state.get("api_health", {})
            api_health.update(health)
            state["api_health"] = api_health
            cls._write(state)
            logger.debug("API 健康狀態已更新: %s", health)

    @classmethod
    def update_crypto_env(
        cls, symbol: str, score: float, regime: str
    ) -> None:
        """更新加密貨幣環境快取。"""
        with cls._lock:
            state = cls.read()
            cache = state.get("crypto_env_cache", {})
            cache[symbol] = {
                "score": round(score, 4),
                "regime": regime,
                "updated": _now_iso(),
            }
            state["crypto_env_cache"] = cache
            cls._write(state)


# CLI 測試
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print("=== 狀態儲存測試 ===")
    s = BotStateStore.read()
    print(f"初始狀態: {json.dumps(s, indent=2, ensure_ascii=False)}")

    BotStateStore.update(last_confidence_score=0.65, last_confidence_regime="NORMAL")
    BotStateStore.increment("signals_generated_today", 1)
    BotStateStore.record_filter("confidence_low")
    BotStateStore.record_filter("confidence_low")
    BotStateStore.record_filter("volatility_high")

    s = BotStateStore.read()
    print(f"\n更新後狀態: {json.dumps(s, indent=2, ensure_ascii=False)}")
