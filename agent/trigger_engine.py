"""觸發引擎 — 規則判斷是否需要呼叫 Claude，避免無效輪詢。"""

import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class TriggerEngine:
    THRESHOLDS = {
        "consecutive_losses": 3,
        "daily_loss_pct": 0.03,
        "profit_factor_drop": 0.2,
        "win_rate_drop_7d": 0.1,
        "atr_spike_ratio": 1.8,
        "funding_rate_extreme": 0.001,
        "api_errors_1h": 3,
        "bot_silent_minutes": 30,
    }

    COOLDOWN_SECONDS = {
        "consecutive_losses": 3600,
        "daily_loss_pct": 7200,
        "regime_change": 14400,
        "atr_spike": 3600,
        "funding_extreme": 3600,
        "pf_drop": 7200,
        "routine": 0,  # 例行排程無冷卻
    }

    def __init__(self):
        self._last_trigger: dict[str, float] = {}

    def should_invoke_claude(
        self, current_state: dict
    ) -> tuple[bool, str, str, list[str]]:
        """
        判斷是否應呼叫 Claude。
        回傳: (是否呼叫, 主要原因, 優先等級, 所有原因清單)
        """
        all_triggers: list[tuple[str, str, str]] = []  # (優先等級, 訊息, 鍵值)

        # 緊急：立即回傳
        errors_1h = current_state.get("api_errors_1h", 0)
        if errors_1h >= self.THRESHOLDS["api_errors_1h"]:
            return True, f"API 錯誤 {errors_1h} 次/小時", "critical", ["api_errors"]

        bot_silent = current_state.get("bot_silent_minutes", 0)
        if bot_silent >= self.THRESHOLDS["bot_silent_minutes"]:
            return True, f"Bot 靜默 {bot_silent} 分鐘", "critical", ["bot_silent"]

        # 高優先
        consec = current_state.get("consecutive_losses", 0)
        if consec >= self.THRESHOLDS["consecutive_losses"]:
            all_triggers.append(("high", f"連虧 {consec} 筆", "consecutive_losses"))

        daily_loss = current_state.get("daily_loss_pct", 0)
        if daily_loss >= self.THRESHOLDS["daily_loss_pct"]:
            all_triggers.append(("high", f"日虧 {daily_loss*100:.1f}%", "daily_loss_pct"))

        if current_state.get("regime_just_changed", False):
            all_triggers.append(("high", "市場機制切換", "regime_change"))

        # 中優先
        atr_ratio = current_state.get("atr_spike_ratio", 1.0)
        if atr_ratio >= self.THRESHOLDS["atr_spike_ratio"]:
            all_triggers.append(("medium", f"波動飆升 {atr_ratio:.1f}x", "atr_spike"))

        funding = abs(current_state.get("funding_rate", 0))
        if funding >= self.THRESHOLDS["funding_rate_extreme"]:
            all_triggers.append(("medium", f"資金費率極端 {funding*100:.3f}%", "funding_extreme"))

        pf_drop = current_state.get("profit_factor_drop_7d", 0)
        if pf_drop >= self.THRESHOLDS["profit_factor_drop"]:
            all_triggers.append(("medium", f"PF 下降 {pf_drop*100:.0f}%", "pf_drop"))

        # 例行排程（防止飢餓）
        hours_since = self._hours_since(current_state.get("last_routine_analysis"))
        if hours_since >= 36:
            all_triggers.insert(0, ("high", "例行分析延遲 36h（強制）", "routine"))
        elif hours_since >= 24:
            all_triggers.append(("routine", "每日例行分析", "routine"))

        if not all_triggers:
            return False, "無觸發", "none", []

        # 冷卻過濾
        now = time.time()
        active = []
        for prio, msg, key in all_triggers:
            cooldown = self.COOLDOWN_SECONDS.get(key, 3600)
            last = self._last_trigger.get(key, 0)
            if now - last >= cooldown:
                active.append((prio, msg, key))

        if not active:
            return False, "冷卻期中", "none", []

        # 依優先等級排序
        prio_order = ["critical", "high", "medium", "routine"]
        active.sort(key=lambda x: prio_order.index(x[0]))

        # 標記已觸發
        for _, _, key in active:
            self._last_trigger[key] = now

        main = active[0]
        all_reasons = [t[1] for t in active]
        return True, main[1], main[0], all_reasons

    def _hours_since(self, iso_timestamp: Optional[str]) -> float:
        """計算距離指定 ISO 時間戳已過的小時數。"""
        if not iso_timestamp:
            return 999
        try:
            dt = datetime.fromisoformat(iso_timestamp)
            return (datetime.now() - dt).total_seconds() / 3600
        except (ValueError, TypeError):
            return 999
