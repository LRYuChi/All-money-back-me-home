"""Agent Memory — SQLite-based decision logging and knowledge store.

All agent decisions are immutably recorded for:
1. Audit trail
2. Learning engine input (Phase 2)
3. Performance tracking
"""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"


class AgentMemory:
    """Persistent memory for agent decisions and knowledge."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    context TEXT,  -- JSON
                    regime TEXT,
                    outcome_7d TEXT,   -- filled later
                    outcome_30d TEXT,  -- filled later
                    was_successful INTEGER  -- 0/1, filled later
                );

                CREATE TABLE IF NOT EXISTS knowledge (
                    id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    regime TEXT,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,  -- JSON
                    source TEXT  -- "learning_engine" or "manual"
                );

                CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(timestamp);
                CREATE INDEX IF NOT EXISTS idx_decisions_regime ON decisions(regime);
                CREATE INDEX IF NOT EXISTS idx_knowledge_regime ON knowledge(regime);
            """)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path))

    # ------------------------------------------------------------------
    # Decision Logging
    # ------------------------------------------------------------------

    def log_decision(
        self,
        action: str,
        reason: str,
        confidence: float,
        context: dict | None = None,
        regime: str | None = None,
    ) -> str:
        """Log an agent decision. Returns decision ID."""
        decision_id = str(uuid.uuid4())[:8]
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO decisions (id, timestamp, action, reason, confidence, context, regime)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision_id,
                    time.time(),
                    action,
                    reason,
                    confidence,
                    json.dumps(context or {}),
                    regime,
                ),
            )
        logger.info("Decision logged: %s — %s (conf=%.2f)", decision_id, action, confidence)
        return decision_id

    def get_decisions(self, limit: int = 50, regime: str | None = None) -> list[dict]:
        """Get recent decisions."""
        with self._connect() as conn:
            if regime:
                rows = conn.execute(
                    "SELECT * FROM decisions WHERE regime = ? ORDER BY timestamp DESC LIMIT ?",
                    (regime, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM decisions ORDER BY timestamp DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update_outcome(self, decision_id: str, outcome_7d: str | None = None,
                       outcome_30d: str | None = None, was_successful: bool | None = None) -> None:
        """Update a decision with its outcome (called by outcome_tracker)."""
        updates = []
        params = []
        if outcome_7d is not None:
            updates.append("outcome_7d = ?")
            params.append(outcome_7d)
        if outcome_30d is not None:
            updates.append("outcome_30d = ?")
            params.append(outcome_30d)
        if was_successful is not None:
            updates.append("was_successful = ?")
            params.append(1 if was_successful else 0)

        if updates:
            params.append(decision_id)
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE decisions SET {', '.join(updates)} WHERE id = ?",
                    params,
                )

    # ------------------------------------------------------------------
    # Knowledge Store
    # ------------------------------------------------------------------

    def add_knowledge(self, category: str, content: dict,
                      regime: str | None = None, source: str = "learning_engine") -> str:
        """Add a knowledge entry."""
        kid = str(uuid.uuid4())[:8]
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO knowledge (id, created_at, updated_at, regime, category, content, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (kid, now, now, regime, category, json.dumps(content), source),
            )
        return kid

    def get_knowledge(self, regime: str | None = None, category: str | None = None) -> list[dict]:
        """Get knowledge entries, optionally filtered."""
        with self._connect() as conn:
            query = "SELECT * FROM knowledge WHERE 1=1"
            params: list[Any] = []
            if regime:
                query += " AND regime = ?"
                params.append(regime)
            if category:
                query += " AND category = ?"
                params.append(category)
            query += " ORDER BY updated_at DESC"
            rows = conn.execute(query, params).fetchall()
        return [self._knowledge_to_dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Get memory statistics."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            with_outcome = conn.execute("SELECT COUNT(*) FROM decisions WHERE was_successful IS NOT NULL").fetchone()[0]
            knowledge_count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        return {
            "total_decisions": total,
            "decisions_with_outcome": with_outcome,
            "knowledge_entries": knowledge_count,
        }

    # ------------------------------------------------------------------
    # Smart Retrieval (forgetting curve)
    # ------------------------------------------------------------------

    def retrieve_relevant(self, regime: str, domain: str = None, limit: int = 10) -> list[dict]:
        """智慧檢索：結合 regime 匹配 + 使用頻率 + 新鮮度 + 成功率"""
        conditions = ["archived = 0"]
        params = []
        if domain:
            conditions.append("domain = ?")
            params.append(domain)

        sql = f"""
            SELECT *,
                julianday('now') - julianday(COALESCE(last_accessed, timestamp)) as days_since_access
            FROM decisions
            WHERE {' AND '.join(conditions)}
            ORDER BY timestamp DESC
            LIMIT 200
        """
        rows = self._query(sql, params)

        scored = []
        for row in rows:
            r = dict(row) if not isinstance(row, dict) else row
            regime_score = 3.0 if r.get("regime") == regime else 1.0
            freq_score = math.log((r.get("access_count", 0) or 0) + 1)
            days = r.get("days_since_access", 30) or 30
            recency_score = 1.0 / (days + 1)
            success = r.get("was_successful")
            success_score = 1.5 if success == 1 else (0.5 if success == 0 else 1.0)

            total = regime_score * 3 + freq_score * 2 + recency_score * 1 + success_score * 2
            scored.append((total, r))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Touch top results (increment access_count)
        result = []
        for _, r in scored[:limit]:
            self._touch_decision(r.get("id"))
            result.append(r)
        return result

    def _touch_decision(self, decision_id: str):
        """更新 access_count 和 last_accessed（越用越容易被找到）"""
        if not decision_id:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            with self._connect() as conn:
                conn.execute(
                    "UPDATE decisions SET access_count = COALESCE(access_count, 0) + 1, last_accessed = ? WHERE id = ?",
                    (now, decision_id),
                )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bidirectional Links (Obsidian-style)
    # ------------------------------------------------------------------

    def add_link(self, source_type: str, source_id: str, target_type: str, target_id: str, relation: str):
        """建立雙向連結（Obsidian 式）"""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO links VALUES (?, ?, ?, ?, ?, ?)",
                (source_type, source_id, target_type, target_id, relation, now),
            )

    def get_related(self, item_type: str, item_id: str) -> list[dict]:
        """取得所有相關項目（雙向查詢）"""
        rows = self._query("""
            SELECT * FROM links
            WHERE (source_type=? AND source_id=?) OR (target_type=? AND target_id=?)
        """, (item_type, item_id, item_type, item_id))
        return [dict(r) if not isinstance(r, dict) else r for r in rows]

    # ------------------------------------------------------------------
    # Knowledge CRUD (v2)
    # ------------------------------------------------------------------

    def upsert_knowledge(self, domain: str, title: str, content: str, regime: str = None,
                         confidence: float = 0.5, evidence_count: int = 1):
        """新增或更新知識規則"""
        now = datetime.now(timezone.utc).isoformat()
        existing = self._query(
            "SELECT id, evidence_count, version FROM knowledge WHERE title = ? AND domain = ?",
            (title, domain),
        )
        with self._connect() as conn:
            if existing:
                row = existing[0]
                r = dict(row) if not isinstance(row, dict) else row
                conn.execute("""
                    UPDATE knowledge SET content=?, confidence=?, evidence_count=?,
                    version=?, updated_at=? WHERE id=?
                """, (content, confidence, evidence_count,
                      (r.get("version", 1) or 1) + 1, now, r.get("id")))
            else:
                conn.execute("""
                    INSERT INTO knowledge (id, domain, regime, title, content, confidence,
                    evidence_count, access_count, version, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                """, (str(uuid.uuid4())[:12], domain, regime, title, content, confidence,
                      evidence_count, now, now))

    def get_knowledge_v2(self, domain: str = None, regime: str = None, limit: int = 10) -> list[dict]:
        """取得知識規則（按信心排序）— v2 schema"""
        conditions = []
        params: list[Any] = []
        if domain:
            conditions.append("domain = ?")
            params.append(domain)
        if regime:
            conditions.append("(regime = ? OR regime IS NULL)")
            params.append(regime)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = self._query(f"SELECT * FROM knowledge {where} ORDER BY confidence DESC, access_count DESC LIMIT ?", params)
        return [dict(r) if not isinstance(r, dict) else r for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _query(self, sql: str, params: tuple = ()) -> list:
        """Execute a read query and return rows."""
        try:
            with self._connect() as conn:
                conn.row_factory = sqlite3.Row
                return conn.execute(sql, params).fetchall()
        except Exception as e:
            logger.warning("Query failed: %s", e)
            return []

    @staticmethod
    def _row_to_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "timestamp": row[1],
            "action": row[2],
            "reason": row[3],
            "confidence": row[4],
            "context": json.loads(row[5]) if row[5] else {},
            "regime": row[6],
            "outcome_7d": row[7],
            "outcome_30d": row[8],
            "was_successful": bool(row[9]) if row[9] is not None else None,
        }

    @staticmethod
    def _knowledge_to_dict(row: tuple) -> dict:
        return {
            "id": row[0],
            "created_at": row[1],
            "updated_at": row[2],
            "regime": row[3],
            "category": row[4],
            "content": json.loads(row[5]) if row[5] else {},
            "source": row[6],
        }
