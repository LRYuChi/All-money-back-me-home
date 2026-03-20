"""幻覺防護 — 三層校驗：輸入新鮮度、輸出結構、決策後驗證。"""

import json
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"


class HallucinationGuard:

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_verifications (
                    id TEXT PRIMARY KEY,
                    decision_id TEXT NOT NULL,
                    verify_at TEXT NOT NULL,
                    check_type TEXT NOT NULL,
                    baseline_metrics TEXT,
                    expected_outcome TEXT,
                    completed INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("幻覺防護表建立失敗: %s", e)

    # ─── 第一層：輸入新鮮度 ───
    def build_grounded_prompt(self, data: dict) -> str:
        """構建帶數據新鮮度報告的 Prompt 前綴"""
        TTL_MAP = {
            "confidence": 3600,
            "crypto_env": 7200,
            "funding_rate": 3600,
            "macro": 14400,
            "news": 14400,
        }
        lines = []
        stale_count = 0
        for key, ttl in TTL_MAP.items():
            val = data.get(key)
            if isinstance(val, dict) and "timestamp" in val:
                age = time.time() - val["timestamp"]
                status = "✅" if age < ttl else "⚠️ STALE"
                if age >= ttl:
                    stale_count += 1
                lines.append(f"  {key}: {status} ({age/60:.0f}分鐘前)")
            elif val is None:
                lines.append(f"  {key}: ❌ N/A")
                stale_count += 1

        quality = "good" if stale_count == 0 else ("degraded" if stale_count <= 2 else "poor")
        header = f"⚠️ 數據品質: {quality} ({stale_count} 項過期/缺失)\n"
        header += "\n".join(lines) if lines else "  (無數據源資訊)"
        header += "\n\n約束："
        header += "\n- STALE 數據不得作為主要決策依據"
        header += "\n- N/A 數據 → 結論必須含「數據不足，建議觀望」"
        header += "\n- 所有數值引用必須來自以下數據\n"
        return header

    # ─── 第二層：輸出結構校驗 ───
    def validate_decision(self, decision: dict, raw_data: dict) -> tuple[bool, list[str]]:
        """校驗 Agent 決策的結構和引用"""
        errors = []

        # 必要欄位檢查
        for field in ["action", "reason", "confidence"]:
            if field not in decision:
                errors.append(f"缺少必要欄位: {field}")

        # 信心值合理性檢查
        conf = decision.get("confidence", 0)
        citations = decision.get("data_citations", [])
        if conf > 0.85 and len(citations) < 3:
            errors.append(f"高信心({conf:.2f})但僅{len(citations)}個數據引用")

        # 行動白名單檢查
        valid_actions = {"adjust_params", "switch_strategy", "adjust_risk",
                         "pause_bot", "send_alert", "no_action"}
        action = decision.get("action", "")
        if action and action not in valid_actions:
            errors.append(f"無效 action: {action}")

        # 不確定語言偵測
        uncertain = ["可能", "或許", "大概", "應該", "maybe", "probably"]
        reason = decision.get("reason", "")
        for word in uncertain:
            if word in reason:
                decision["_has_uncertain_language"] = True
                break

        return len(errors) == 0, errors

    # ─── 第三層：決策後驗證排程 ───
    def schedule_verification(self, decision_id: str, decision: dict,
                              current_metrics: dict):
        """決策後排程 24/48/168h 驗證"""
        intervals = [
            (24, "short_term"),
            (48, "medium_term"),
            (168, "weekly"),
        ]
        try:
            conn = sqlite3.connect(str(DB_PATH))
            for hours, check_type in intervals:
                verify_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                conn.execute(
                    "INSERT OR IGNORE INTO pending_verifications VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4())[:12], decision_id, verify_at, check_type,
                     json.dumps(current_metrics), decision.get("reason", ""), 0)
                )
            conn.commit()
            conn.close()
            logger.info("已排程 3 筆驗證 (24/48/168h) for decision %s", decision_id)
        except Exception as e:
            logger.warning("排程驗證失敗: %s", e)

    def run_pending_verifications(self, current_metrics: dict) -> list[dict]:
        """處理到期的驗證任務"""
        results = []
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            due = conn.execute("""
                SELECT * FROM pending_verifications
                WHERE verify_at <= ? AND completed = 0
            """, (datetime.now(timezone.utc).isoformat(),)).fetchall()

            for task in due:
                baseline = json.loads(task["baseline_metrics"]) if task["baseline_metrics"] else {}
                outcome = {
                    "pf_change": current_metrics.get("profit_factor", 1.0) - baseline.get("profit_factor", 1.0),
                    "wr_change": current_metrics.get("win_rate", 0.5) - baseline.get("win_rate", 0.5),
                    "dd_change": current_metrics.get("max_drawdown", 0) - baseline.get("max_drawdown", 0),
                }
                was_effective = (outcome["pf_change"] >= -0.1 and outcome["dd_change"] <= 0.02)

                conn.execute(
                    "UPDATE pending_verifications SET completed = 1 WHERE id = ?",
                    (task["id"],)
                )
                results.append({
                    "decision_id": task["decision_id"],
                    "check_type": task["check_type"],
                    "outcome": outcome,
                    "was_effective": was_effective,
                })

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("驗證執行失敗: %s", e)
        return results
