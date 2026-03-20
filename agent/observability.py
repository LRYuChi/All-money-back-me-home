"""可觀測性 — 決策追蹤鏈 + 系統健康儀表板。"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"


class Observability:

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS decision_traces (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    inputs TEXT,
                    reasoning TEXT,
                    output TEXT,
                    skills_used TEXT,
                    tokens_input INTEGER DEFAULT 0,
                    tokens_output INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    model TEXT,
                    priority TEXT,
                    trigger_reason TEXT,
                    load_method TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("可觀測性表建立失敗: %s", e)

    def emit_trace(
        self,
        inputs: dict,
        reasoning: dict,
        output: dict,
        skills_used: list[str] = None,
        tokens_input: int = 0,
        tokens_output: int = 0,
        cost_usd: float = 0,
        model: str = "",
        priority: str = "",
        trigger_reason: str = "",
        load_method: str = "normal",
    ):
        """記錄完整的決策追蹤鏈"""
        import uuid
        trace_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                """INSERT INTO decision_traces VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trace_id, now,
                 json.dumps(inputs, ensure_ascii=False, default=str)[:5000],
                 json.dumps(reasoning, ensure_ascii=False, default=str)[:3000],
                 json.dumps(output, ensure_ascii=False, default=str)[:2000],
                 json.dumps(skills_used or []),
                 tokens_input, tokens_output, cost_usd, model, priority,
                 trigger_reason, load_method)
            )
            conn.commit()
            conn.close()
            logger.debug("決策追蹤已記錄: %s", trace_id)
        except Exception as e:
            logger.warning("追蹤記錄失敗: %s", e)

    def get_system_health(self) -> dict:
        """系統健康儀表板"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row

            # Agent 健康狀態
            traces_24h = conn.execute("""
                SELECT COUNT(*) as cnt,
                    AVG(tokens_input + tokens_output) as avg_tokens,
                    SUM(cost_usd) as total_cost
                FROM decision_traces
                WHERE timestamp >= datetime('now', '-1 day')
            """).fetchone()

            errors_24h = conn.execute("""
                SELECT COUNT(*) as cnt FROM decision_traces
                WHERE timestamp >= datetime('now', '-1 day')
                AND json_extract(output, '$.action') = 'no_action'
                AND json_extract(output, '$.reason') LIKE '%失敗%'
            """).fetchone()

            # 記憶體健康狀態
            total_decisions = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            active_knowledge = conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE confidence > 0.2"
            ).fetchone()[0] if self._table_exists(conn, "knowledge") else 0

            # 待驗證任務數量
            pending = conn.execute("""
                SELECT COUNT(*) FROM pending_verifications WHERE completed = 0
            """).fetchone()[0] if self._table_exists(conn, "pending_verifications") else 0

            conn.close()

            return {
                "agent_health": {
                    "api_calls_24h": traces_24h["cnt"] if traces_24h else 0,
                    "avg_tokens": round(traces_24h["avg_tokens"] or 0) if traces_24h else 0,
                    "cost_24h_usd": round(traces_24h["total_cost"] or 0, 4) if traces_24h else 0,
                    "error_count_24h": errors_24h["cnt"] if errors_24h else 0,
                },
                "memory_health": {
                    "total_decisions": total_decisions,
                    "active_knowledge_rules": active_knowledge,
                    "pending_verifications": pending,
                },
                "cost_estimate": {
                    "daily_usd": round((traces_24h["total_cost"] or 0) if traces_24h else 0, 3),
                    "monthly_usd": round(((traces_24h["total_cost"] or 0) * 30) if traces_24h else 0, 2),
                },
            }
        except Exception as e:
            logger.warning("健康檢查失敗: %s", e)
            return {"error": str(e)}

    def get_daily_summary(self) -> str:
        """每日摘要（Telegram 報告）"""
        health = self.get_system_health()
        ah = health.get("agent_health", {})
        mh = health.get("memory_health", {})
        cost = health.get("cost_estimate", {})

        return (
            f"🔬 *Agent 可觀測性日報*\n"
            f"━━━━━━━━━━━━━━\n"
            f"API 呼叫: {ah.get('api_calls_24h', 0)} 次\n"
            f"均 Token: {ah.get('avg_tokens', 0)}\n"
            f"今日費用: ${cost.get('daily_usd', 0):.3f}\n"
            f"月估費用: ${cost.get('monthly_usd', 0):.2f}\n"
            f"錯誤: {ah.get('error_count_24h', 0)} 次\n"
            f"決策總數: {mh.get('total_decisions', 0)}\n"
            f"知識規則: {mh.get('active_knowledge_rules', 0)}\n"
            f"待驗證: {mh.get('pending_verifications', 0)}"
        )

    @staticmethod
    def _table_exists(conn, table_name: str) -> bool:
        """檢查資料表是否存在"""
        try:
            conn.execute(f"SELECT 1 FROM {table_name} LIMIT 1")
            return True
        except sqlite3.OperationalError:
            return False
