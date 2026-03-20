#!/usr/bin/env python3
"""記憶整理排程 — 每週執行，冪等性保護。"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)
DATA_DIR = Path(os.environ.get("DATA_DIR", str(PROJECT_ROOT / "data")))
LOCK_PATH = DATA_DIR / "consolidation.lock"
TIMESTAMP_PATH = DATA_DIR / "last_consolidation.txt"


def _is_same_week(last_run_iso: str) -> bool:
    """檢查上次執行是否在同一週內（冪等性判斷）"""
    try:
        last = datetime.fromisoformat(last_run_iso)
        now = datetime.now(timezone.utc)
        return last.isocalendar()[1] == now.isocalendar()[1] and last.year == now.year
    except Exception:
        return False


def main(dry_run: bool = False):
    """主流程：歸檔、刪除、信心衰減、知識提煉"""
    # 冪等性檢查
    if TIMESTAMP_PATH.exists():
        last_run = TIMESTAMP_PATH.read_text().strip()
        if _is_same_week(last_run):
            print(f"本週已於 {last_run} 執行過，跳過")
            return

    # 鎖定機制，防止同時執行
    if LOCK_PATH.exists():
        lock_age = time.time() - LOCK_PATH.stat().st_mtime
        if lock_age < 3600:
            print("另一個整理進程正在執行，退出")
            return

    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.touch()

        from agent.memory import AgentMemory
        memory = AgentMemory()

        results = {"archived": 0, "decayed": 0, "deleted": 0, "rules_extracted": 0}

        if dry_run:
            print("DRY RUN 模式")
            # 預覽模式：僅顯示預估數量
            import sqlite3
            db_path = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"
            if not db_path.exists():
                print("數據庫不存在")
                return
            conn = sqlite3.connect(str(db_path))
            archivable = conn.execute(
                "SELECT COUNT(*) FROM decisions "
                "WHERE julianday('now') - julianday(timestamp) > 90 "
                "AND COALESCE(access_count, 0) < 3 "
                "AND COALESCE(archived, 0) = 0"
            ).fetchone()[0]
            deletable = conn.execute(
                "SELECT COUNT(*) FROM decisions "
                "WHERE COALESCE(archived, 1) = 1 "
                "AND julianday('now') - julianday(timestamp) > 180"
            ).fetchone()[0]
            conn.close()
            print(f"預覽: 可歸檔 {archivable} 筆, 可刪除 {deletable} 筆")
            return

        # 步驟一：歸檔超過 90 天且低存取的決策
        try:
            import sqlite3
            db_path = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("""
                UPDATE decisions SET archived = 1
                WHERE julianday('now') - julianday(timestamp) > 90
                AND COALESCE(access_count, 0) < 3
                AND COALESCE(archived, 0) = 0
            """)
            results["archived"] = cursor.rowcount
            print(f"歸檔: {results['archived']} 筆")

            # 步驟二：刪除超過 180 天的已歸檔決策
            cursor = conn.execute("""
                DELETE FROM decisions
                WHERE COALESCE(archived, 0) = 1
                AND julianday('now') - julianday(timestamp) > 180
            """)
            results["deleted"] = cursor.rowcount
            print(f"刪除: {results['deleted']} 筆")

            # 步驟三：對超過 30 天未驗證的知識進行信心衰減
            cursor = conn.execute("""
                UPDATE knowledge SET confidence = confidence * 0.8
                WHERE julianday('now') - julianday(COALESCE(last_validated, updated_at, created_at)) > 30
                AND confidence > 0.2
            """)
            results["decayed"] = cursor.rowcount
            print(f"信心衰減: {results['decayed']} 筆")

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("整理操作失敗: %s", e)

        # 步驟四：針對各市場環境進行知識提煉
        try:
            from agent.knowledge_extractor import KnowledgeExtractor
            extractor = KnowledgeExtractor(memory)
            for regime in ["TRENDING_BULL", "TRENDING_BEAR", "HIGH_VOLATILITY", "RANGING"]:
                result = extractor.extract(regime=regime, days=30)
                rules_count = len(result.get("rules", []))
                results["rules_extracted"] += rules_count
                if rules_count:
                    print(f"  {regime}: {rules_count} 條規則")
        except Exception as e:
            logger.warning("知識提煉失敗: %s", e)

        # 記錄完成時間
        TIMESTAMP_PATH.parent.mkdir(parents=True, exist_ok=True)
        TIMESTAMP_PATH.write_text(datetime.now(timezone.utc).isoformat())

        print(f"\n整理完成: {json.dumps(results, ensure_ascii=False)}")

        # 透過 Telegram 通知整理結果
        try:
            from market_monitor.telegram_zh import send_message
            send_message(
                f"🧹 *記憶整理完成*\n"
                f"歸檔: {results['archived']} | 刪除: {results['deleted']}\n"
                f"信心衰減: {results['decayed']} | 新規則: {results['rules_extracted']}"
            )
        except Exception:
            pass

    finally:
        # 確保鎖定檔案被清除
        LOCK_PATH.unlink(missing_ok=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main(dry_run="--dry-run" in sys.argv)
