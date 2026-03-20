"""Skills 動態載入引擎 — 按需載入、三層降級、Token 預算控制。"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# 硬編碼降級 Skill（不依賴任何外部文件）
FALLBACK_SKILL = """## Emergency Fallback Mode
你處於降級模式，只執行最保守的操作。
規則：
- 連虧 >= 3 → pause_bot
- 日虧 >= 3% → adjust_risk(minimal)
- 其他 → no_action + send_alert
輸出：{"a":"no_action","c":{},"r":"降級模式","conf":0.5,"h":true,"cite":[]}
"""

# Skill token 預算（從 REGISTRY 載入，fallback 硬編碼）
_DEFAULT_SIZES = {
    "core/SAFETY": 150, "core/OUTPUT_FORMAT": 80,
    "perception/MARKET_READ": 200, "perception/PERFORMANCE_READ": 180,
    "perception/REGIME_READ": 160,
    "decision/RISK_DECISION": 220, "decision/PARAM_ADJUST": 190,
    "decision/STRATEGY_SWITCH": 170, "decision/EMERGENCY": 140,
    "analysis/DAILY_ANALYSIS": 300, "analysis/KNOWLEDGE_EXTRACT": 280,
    "analysis/RETROSPECTIVE": 250,
    "meta/SKILL_SELECTOR": 200, "meta/HALLUCINATION_GUARD": 120,
}

ALWAYS_INCLUDE = {"core/SAFETY", "core/OUTPUT_FORMAT"}

# 優先等級 → skill 對應（規則式選擇，不需要 LLM）
PRIORITY_SKILLS = {
    "critical": ["decision/EMERGENCY", "perception/MARKET_READ"],
    "high": ["decision/RISK_DECISION", "perception/PERFORMANCE_READ"],
    "medium": ["decision/PARAM_ADJUST"],
    "routine": ["analysis/DAILY_ANALYSIS", "perception/MARKET_READ",
                "perception/PERFORMANCE_READ", "perception/REGIME_READ"],
}

TRIGGER_SKILLS = {
    "consecutive_losses": ["decision/RISK_DECISION"],
    "regime_change": ["perception/REGIME_READ", "decision/STRATEGY_SWITCH"],
    "knowledge_extract": ["analysis/KNOWLEDGE_EXTRACT"],
    "system_error": ["decision/EMERGENCY"],
    "weekly_review": ["analysis/RETROSPECTIVE"],
    "routine_overdue": ["analysis/DAILY_ANALYSIS"],
}


class SkillLoader:
    def __init__(self):
        self._skill_sizes = self._load_registry_sizes()
        self._last_good_cache: dict[str, str] = {}  # 優先等級 → 快取內容

    def _load_registry_sizes(self) -> dict[str, int]:
        """從 SKILL_REGISTRY.json 載入 token 大小，失敗則使用預設值。"""
        registry_path = SKILLS_DIR / "SKILL_REGISTRY.json"
        try:
            if registry_path.exists():
                data = json.loads(registry_path.read_text(encoding="utf-8"))
                return {k: v["token_size"] for k, v in data.get("skills", {}).items()}
        except Exception as e:
            logger.warning("Registry 載入失敗: %s, 使用預設值", e)
        return _DEFAULT_SIZES.copy()

    def select_skills(
        self, trigger_reason: str, priority: str, regime: str,
        token_budget: int = 800
    ) -> list[str]:
        """規則式 skill 選擇（0 LLM tokens）。"""
        selected = set(ALWAYS_INCLUDE)

        # 依優先等級選擇
        selected.update(PRIORITY_SKILLS.get(priority, []))

        # 依觸發關鍵字選擇
        trigger_lower = trigger_reason.lower()
        for keyword, skills in TRIGGER_SKILLS.items():
            if keyword.replace("_", " ") in trigger_lower or keyword in trigger_lower:
                selected.update(skills)

        # 永遠加入幻覺防護
        selected.add("meta/HALLUCINATION_GUARD")

        # 裁剪至預算上限
        return self._trim_to_budget(list(selected), token_budget)

    def load_with_fallback(
        self, trigger_reason: str, priority: str, regime: str,
        token_budget: int = 800
    ) -> tuple[str, str]:
        """三層降級：正常 → 快取 → 硬編碼。"""
        # 第一層：正常載入
        try:
            skills = self.select_skills(trigger_reason, priority, regime, token_budget)
            context = self._load_skills(skills)
            if context:
                self._last_good_cache[priority] = context
                return context, "normal"
        except Exception as e:
            logger.error("Skills 正常載入失敗: %s", e)

        # 第二層：快取
        cached = self._last_good_cache.get(priority)
        if cached:
            logger.warning("使用快取 Skills (priority=%s)", priority)
            return cached, "cached"

        # 第三層：硬編碼降級
        logger.error("降級至 Fallback Skill")
        return FALLBACK_SKILL, "fallback"

    def _load_skills(self, skill_names: list[str]) -> str:
        """從檔案系統載入指定的 skill 內容。"""
        parts = []
        for name in skill_names:
            path = SKILLS_DIR / f"{name}.md"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                parts.append(f"## [{name}]\n{content}")
            else:
                logger.warning("Skill 不存在: %s", name)
        return "\n\n---\n\n".join(parts)

    def _trim_to_budget(self, skills: list[str], budget: int) -> list[str]:
        """依 token 預算裁剪 skill 清單，必要 skill 優先保留。"""
        result = [s for s in skills if s in ALWAYS_INCLUDE]
        used = sum(self._skill_sizes.get(s, 0) for s in result)
        optional = [s for s in skills if s not in ALWAYS_INCLUDE]
        for skill in optional:
            cost = self._skill_sizes.get(skill, 200)
            if used + cost <= budget:
                result.append(skill)
                used += cost
        return result
