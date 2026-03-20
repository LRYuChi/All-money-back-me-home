"""Skill 演化器 — 知識提煉後安全更新 Skills，含回滾保護。"""

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
DB_PATH = Path(os.environ.get("DATA_DIR", "/data")) / "agent_memory.db"

# 每個 Skill 的 Token 大小限制
SKILL_SIZES = {
    "core/SAFETY": 150, "core/OUTPUT_FORMAT": 80,
    "perception/MARKET_READ": 200, "perception/PERFORMANCE_READ": 180,
    "perception/REGIME_READ": 160,
    "decision/RISK_DECISION": 220, "decision/PARAM_ADJUST": 190,
    "decision/STRATEGY_SWITCH": 170, "decision/EMERGENCY": 140,
    "analysis/DAILY_ANALYSIS": 300, "analysis/KNOWLEDGE_EXTRACT": 280,
    "analysis/RETROSPECTIVE": 250,
}


class SkillEvolver:
    """安全地演化 Skill 檔案，包含差異閾值、人工審核與自動回滾機制。"""

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """建立版本管理所需的資料表"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_versions (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    reason TEXT,
                    diff_ratio REAL,
                    deployed_at TEXT,
                    rolled_back INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS skill_rollback_checks (
                    id TEXT PRIMARY KEY,
                    skill_name TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    check_at TEXT NOT NULL,
                    baseline_pf REAL,
                    baseline_wr REAL,
                    completed INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("Skill 版本表建立失敗: %s", e)

    def evolve_safely(self, skill_name: str, new_knowledge: list[dict]) -> dict:
        """安全演化 Skill，含品質檢查和回滾保護"""
        # 前置檢查：知識數量
        if len(new_knowledge) < 3:
            return {"status": "skipped", "reason": "知識數量不足(<3)"}

        # 前置檢查：平均信心度
        avg_conf = sum(k.get("confidence", 0) for k in new_knowledge) / len(new_knowledge)
        if avg_conf < 0.55:
            return {"status": "skipped", "reason": f"平均信心太低({avg_conf:.2f})"}

        # 前置檢查：證據總數
        evidence_total = sum(k.get("evidence_count", 0) for k in new_knowledge)
        if evidence_total < 10:
            return {"status": "skipped", "reason": f"證據不足({evidence_total}筆)"}

        # 讀取目前 Skill 內容
        skill_path = SKILLS_DIR / f"{skill_name}.md"
        if not skill_path.exists():
            return {"status": "error", "reason": f"Skill 不存在: {skill_name}"}

        current_content = skill_path.read_text(encoding="utf-8")

        # 產生候選內容（附加新規則段落）
        new_rules_text = "\n\n## 學習規則 (自動生成)\n"
        for k in new_knowledge:
            new_rules_text += f"- **{k.get('title', '?')}**: {k.get('condition', '?')} → {k.get('expected_outcome', '?')} (信心:{k.get('confidence', 0):.2f})\n"

        candidate = current_content.rstrip() + new_rules_text

        # 檢查 Token 預算，超出時僅保留信心最高的前 3 條規則
        max_tokens = SKILL_SIZES.get(skill_name, 200)
        est_tokens = len(candidate.split()) * 1.3
        if est_tokens > max_tokens * 1.5:
            new_rules_text = "\n\n## 學習規則 (自動生成)\n"
            sorted_knowledge = sorted(new_knowledge, key=lambda x: x.get("confidence", 0), reverse=True)
            for k in sorted_knowledge[:3]:
                new_rules_text += f"- **{k.get('title', '?')}**: {k.get('condition', '?')} (信心:{k.get('confidence', 0):.2f})\n"
            candidate = current_content.rstrip() + new_rules_text

        # 計算差異比例
        diff_ratio = self._calc_diff_ratio(current_content, candidate)

        # 差異過大（>30%）需要人工審核
        if diff_ratio > 0.30:
            self._notify_human_review(skill_name, diff_ratio, new_knowledge)
            return {"status": "pending_review", "diff_ratio": diff_ratio}

        # 部署新版本
        version_id = self._backup_and_deploy(skill_name, current_content, candidate, diff_ratio)
        self._schedule_rollback_check(skill_name, version_id)

        return {"status": "deployed", "diff_ratio": diff_ratio, "version_id": version_id}

    def _calc_diff_ratio(self, old: str, new: str) -> float:
        """計算新舊內容的差異比例"""
        old_lines = set(old.strip().split("\n"))
        new_lines = set(new.strip().split("\n"))
        if not old_lines:
            return 1.0
        changed = len(old_lines.symmetric_difference(new_lines))
        return changed / max(len(old_lines), len(new_lines))

    def _backup_and_deploy(self, skill_name: str, old_content: str, new_content: str, diff_ratio: float) -> str:
        """備份舊版本並部署新內容"""
        version_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()

        # 將舊版本存入資料庫
        try:
            conn = sqlite3.connect(str(DB_PATH))
            # 取得目前最新版本號
            row = conn.execute(
                "SELECT MAX(version) FROM skill_versions WHERE skill_name = ?",
                (skill_name,)
            ).fetchone()
            version_num = (row[0] or 0) + 1

            conn.execute(
                "INSERT INTO skill_versions VALUES (?,?,?,?,?,?,?,?)",
                (version_id, skill_name, version_num, old_content,
                 "auto_evolve", diff_ratio, now, 0)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("版本備份失敗: %s", e)

        # 寫入新內容到 Skill 檔案
        skill_path = SKILLS_DIR / f"{skill_name}.md"
        skill_path.write_text(new_content, encoding="utf-8")

        # 更新 Registry
        self._update_registry(skill_name, version_num)

        logger.info("Skill %s 已更新至 v%d (diff=%.1f%%)", skill_name, version_num, diff_ratio * 100)
        return version_id

    def _schedule_rollback_check(self, skill_name: str, version_id: str, days: int = 7):
        """排程回滾檢查（預設 7 天後執行）"""
        check_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.execute(
                "INSERT INTO skill_rollback_checks VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4())[:12], skill_name, version_id, check_at, None, None, 0)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning("回滾檢查排程失敗: %s", e)

    def rollback(self, skill_name: str, version_id: str):
        """回滾到指定版本"""
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute(
                "SELECT content FROM skill_versions WHERE id = ?", (version_id,)
            ).fetchone()
            if row:
                skill_path = SKILLS_DIR / f"{skill_name}.md"
                skill_path.write_text(row[0], encoding="utf-8")
                conn.execute(
                    "UPDATE skill_versions SET rolled_back = 1 WHERE id = ?", (version_id,)
                )
                conn.commit()
                logger.info("Skill %s 已回滾至版本 %s", skill_name, version_id)
            conn.close()
        except Exception as e:
            logger.error("回滾失敗: %s", e)

    def _notify_human_review(self, skill_name: str, diff_ratio: float, knowledge: list):
        """差異過大時透過 Telegram 通知人工審核"""
        try:
            from market_monitor.telegram_zh import send_message
            rules = "\n".join(f"- {k.get('title', '?')}" for k in knowledge[:5])
            send_message(
                f"⚠️ *Skill 演化需人工審核*\n"
                f"Skill: {skill_name}\n"
                f"變更幅度: {diff_ratio*100:.0f}%（>30%）\n"
                f"新規則:\n{rules}"
            )
        except Exception:
            pass

    def _update_registry(self, skill_name: str, version: int):
        """更新 SKILL_REGISTRY.json 中的版本資訊"""
        registry_path = SKILLS_DIR / "SKILL_REGISTRY.json"
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            if skill_name in data.get("skills", {}):
                data["skills"][skill_name]["version"] = f"1.{version}"
                data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            registry_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.warning("Registry 更新失敗: %s", e)
