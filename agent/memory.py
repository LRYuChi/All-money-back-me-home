"""Agent Memory — SQLite-based decision logging and knowledge store.

All agent decisions are immutably recorded for:
1. Audit trail
2. Learning engine input (Phase 2)
3. Performance tracking
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = Path("/data/agent_memory.db")


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
    # Helpers
    # ------------------------------------------------------------------

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
