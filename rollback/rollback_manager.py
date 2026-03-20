"""回滾管理器 — 監控系統健康，自動回滾失敗的變更。"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"


class RollbackManager:
    """監控 Skill 更新後的績效，自動回滾不良變更。"""

    def check_all(self, current_metrics: dict = None):
        """每小時執行：檢查所有待回滾項目"""
        self._check_skill_rollbacks(current_metrics or {})
        self._check_trigger_engine_health()

    def _check_skill_rollbacks(self, current_metrics: dict):
        """檢查 Skill 更新後的績效是否下降"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            # 查詢所有已到期且未完成的回滾檢查
            pending = conn.execute("""
                SELECT * FROM skill_rollback_checks
                WHERE check_at <= ? AND completed = 0
            """, (datetime.now(timezone.utc).isoformat(),)).fetchall()

            for check in pending:
                baseline_pf = check["baseline_pf"] or 1.0
                current_pf = current_metrics.get("profit_factor", 1.0)

                if current_pf < baseline_pf * 0.8:
                    # PF 下降超過 20% → 自動回滾
                    from agent.skill_evolver import SkillEvolver
                    evolver = SkillEvolver()
                    evolver.rollback(check["skill_name"], check["version_id"])

                    self._notify(
                        f"🔄 *Skill 自動回滾*\n"
                        f"Skill: {check['skill_name']}\n"
                        f"PF: {baseline_pf:.2f} → {current_pf:.2f} (-{(1-current_pf/baseline_pf)*100:.0f}%)"
                    )
                else:
                    # 績效正常，記錄日誌
                    logger.info(
                        "Skill %s 績效正常 (PF: %.2f → %.2f)",
                        check["skill_name"], baseline_pf, current_pf
                    )

                # 標記該檢查為已完成
                conn.execute(
                    "UPDATE skill_rollback_checks SET completed = 1 WHERE id = ?",
                    (check["id"],)
                )

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Skill 回滾檢查失敗: %s", e)

    def _check_trigger_engine_health(self):
        """檢查觸發引擎是否過度抑制（24 小時內零呼叫）"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute("""
                SELECT COUNT(*) as cnt FROM decision_traces
                WHERE timestamp >= datetime('now', '-1 day')
            """).fetchone()
            conn.close()

            if row and row[0] == 0:
                self._notify(
                    "⚠️ *觸發引擎警告*\n"
                    "24小時內 0 次 API 呼叫\n"
                    "可能過度抑制，請檢查 trigger_engine.py"
                )
        except Exception as e:
            logger.warning("觸發引擎健康檢查失敗: %s", e)

    def _notify(self, message: str):
        """透過 Telegram 發送通知"""
        try:
            from market_monitor.telegram_zh import send_message
            send_message(message)
        except Exception:
            logger.warning("通知發送失敗")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    manager = RollbackManager()
    manager.check_all()
