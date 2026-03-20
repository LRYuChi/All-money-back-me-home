"""Token 消耗追蹤 — 每日報告 + 健康指標。"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"


class TokenMetrics:

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS token_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event TEXT NOT NULL,
                    tokens INTEGER DEFAULT 0,
                    cost_usd REAL DEFAULT 0,
                    model TEXT,
                    priority TEXT,
                    action TEXT,
                    trigger_reason TEXT,
                    skills_used TEXT
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Token metrics 表建立失敗: %s", e)

    def record(self, event: str, tokens: int = 0, cost_usd: float = 0,
               model: str = "", priority: str = "", action: str = "",
               trigger_reason: str = "", skills_used: list[str] = None):
        """記錄一次 token 消耗事件"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT INTO token_metrics (timestamp,event,tokens,cost_usd,model,priority,action,trigger_reason,skills_used) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(), event, tokens, cost_usd,
                 model, priority, action, trigger_reason,
                 json.dumps(skills_used or []))
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Token 記錄失敗: %s", e)

    def get_daily_report(self) -> dict:
        """今日消耗報告"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT
                    SUM(tokens) as total_tokens,
                    SUM(cost_usd) as total_cost,
                    COUNT(*) as total_cycles,
                    SUM(CASE WHEN event='skipped' THEN 1 ELSE 0 END) as skipped,
                    SUM(CASE WHEN event='cache_hit' THEN 1 ELSE 0 END) as cache_hits,
                    SUM(CASE WHEN event='api_call' THEN 1 ELSE 0 END) as api_calls,
                    AVG(CASE WHEN event='api_call' THEN tokens END) as avg_tokens
                FROM token_metrics
                WHERE DATE(timestamp) = DATE('now')
            """).fetchone()
            conn.close()

            if not row or not row["total_cycles"]:
                return {"total_tokens": 0, "total_cost": 0, "is_healthy": True}

            total = row["total_cycles"]
            skip_rate = (row["skipped"] or 0) / total if total else 0
            cache_rate = (row["cache_hits"] or 0) / total if total else 0

            return {
                "total_tokens": row["total_tokens"] or 0,
                "total_cost": round(row["total_cost"] or 0, 4),
                "total_cycles": total,
                "skipped": row["skipped"] or 0,
                "cache_hits": row["cache_hits"] or 0,
                "api_calls": row["api_calls"] or 0,
                "avg_tokens_per_call": round(row["avg_tokens"] or 0),
                "skip_rate": f"{skip_rate:.0%}",
                "cache_hit_rate": f"{cache_rate:.0%}",
                "monthly_estimate": round((row["total_cost"] or 0) * 30, 2),
                "is_healthy": (
                    skip_rate > 0.60 and
                    cache_rate > 0.10 and
                    (row["avg_tokens"] or 0) < 1200
                ),
            }
        except Exception as e:
            logger.warning("報告生成失敗: %s", e)
            return {"error": str(e)}

    def get_telegram_summary(self) -> str:
        """Telegram 格式的每日摘要"""
        r = self.get_daily_report()
        if "error" in r:
            return f"📊 Token 報告錯誤: {r['error']}"
        return (
            f"📊 *Token 消耗日報*\n"
            f"━━━━━━━━━━━━━━\n"
            f"總消耗: {r.get('total_tokens', 0):,} tokens\n"
            f"今日費用: ${r.get('total_cost', 0):.3f}\n"
            f"月估費用: ${r.get('monthly_estimate', 0):.2f}\n"
            f"效率指標:\n"
            f"├ 跳過率: {r.get('skip_rate', 'N/A')}（規則過濾）\n"
            f"├ 快取命中: {r.get('cache_hit_rate', 'N/A')}\n"
            f"└ 均呼叫量: {r.get('avg_tokens_per_call', 0)} tokens/次\n"
            f"狀態: {'✅ 健康' if r.get('is_healthy') else '⚠️ 需優化'}"
        )
