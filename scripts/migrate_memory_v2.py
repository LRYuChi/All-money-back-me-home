#!/usr/bin/env python3
"""記憶系統 Schema 遷移 v2 — 安全升級舊數據。"""

import json, logging, os, shutil, sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
DB_PATH = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / "data"))) / "agent_memory.db"

DOMAIN_RULES = {
    "risk": ["pause", "stop", "loss", "drawdown", "circuit", "risk"],
    "signal": ["ote", "bos", "sweep", "fvg", "choch", "signal", "entry"],
    "regime": ["regime", "trend", "market", "structure", "bull", "bear"],
    "execution": ["leverage", "stake", "position", "param", "adjust"],
}

def infer_domain(action: str, reason: str) -> str:
    text = f"{action} {reason}".lower()
    for domain, keywords in DOMAIN_RULES.items():
        if any(kw in text for kw in keywords):
            return domain
    return "general"

def main(dry_run=True):
    import sqlite3
    if not DB_PATH.exists():
        print(f"數據庫不存在: {DB_PATH}, 跳過遷移")
        return

    # Backup
    backup = DB_PATH.with_suffix(f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(DB_PATH, backup)
    print(f"備份: {backup}")

    if dry_run:
        print("DRY RUN 模式")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # ALTER TABLE — safe, idempotent
    alters = [
        "ALTER TABLE decisions ADD COLUMN tags TEXT DEFAULT ''",
        "ALTER TABLE decisions ADD COLUMN domain TEXT DEFAULT 'general'",
        "ALTER TABLE decisions ADD COLUMN access_count INTEGER DEFAULT 0",
        "ALTER TABLE decisions ADD COLUMN last_accessed TEXT",
        "ALTER TABLE decisions ADD COLUMN archived INTEGER DEFAULT 0",
    ]
    for sql in alters:
        try:
            conn.execute(sql)
            print(f"  OK: {sql[:60]}")
        except Exception as e:
            if "duplicate" in str(e).lower():
                print(f"  SKIP: 欄位已存在")
            else:
                raise

    # Create new tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge (
            id TEXT PRIMARY KEY, domain TEXT NOT NULL, regime TEXT,
            title TEXT NOT NULL, content TEXT NOT NULL,
            confidence REAL DEFAULT 0.5, evidence_count INTEGER DEFAULT 1,
            last_validated TEXT, access_count INTEGER DEFAULT 0,
            last_accessed TEXT, version INTEGER DEFAULT 1,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS links (
            source_type TEXT NOT NULL, source_id TEXT NOT NULL,
            target_type TEXT NOT NULL, target_id TEXT NOT NULL,
            relation TEXT NOT NULL, created_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_links_source ON links(source_type, source_id);
        CREATE INDEX IF NOT EXISTS idx_links_target ON links(target_type, target_id);
        CREATE INDEX IF NOT EXISTS idx_knowledge_domain ON knowledge(domain);
        CREATE INDEX IF NOT EXISTS idx_knowledge_regime ON knowledge(regime);
        CREATE INDEX IF NOT EXISTS idx_decisions_domain ON decisions(domain);
    """)
    print("  新表建立完成")

    # Infer domains for old decisions
    rows = conn.execute("SELECT id, action, reason FROM decisions WHERE domain='general'").fetchall()
    for row in rows:
        domain = infer_domain(row["action"] or "", row["reason"] or "")
        conn.execute("UPDATE decisions SET domain=? WHERE id=?", (domain, row["id"]))
    print(f"  Domain 推斷: {len(rows)} 筆")

    conn.commit()
    conn.close()
    print("遷移完成")

if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    logging.basicConfig(level=logging.INFO)
    main(dry_run=dry)
