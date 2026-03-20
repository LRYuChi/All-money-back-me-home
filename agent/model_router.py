"""模型路由器 — 按任務類型和優先級選擇最佳模型。"""

import logging

logger = logging.getLogger(__name__)

MODEL_CONFIG = {
    "monitoring": "claude-haiku-4-5",
    "decision_normal": "claude-haiku-4-5",
    "decision_critical": "claude-sonnet-4-5",
    "daily_analysis": "claude-sonnet-4-5",
    "knowledge_extract": "claude-sonnet-4-5",
    "skill_selector": "claude-haiku-4-5",
    "news_summary": "claude-haiku-4-5",
}

COST_PER_MTOK = {
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
}


class ModelRouter:

    def get_model(self, task: str, priority: str = "medium") -> str:
        """根據任務和優先級選擇模型"""
        if priority == "critical":
            return "claude-sonnet-4-5"
        return MODEL_CONFIG.get(task, "claude-haiku-4-5")

    def estimate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """估算費用（USD）"""
        costs = COST_PER_MTOK.get(model, COST_PER_MTOK["claude-haiku-4-5"])
        return (
            input_tokens / 1_000_000 * costs["input"] +
            output_tokens / 1_000_000 * costs["output"]
        )

    def get_max_output_tokens(self, priority: str) -> int:
        """按優先級限制輸出 token"""
        return {
            "critical": 500,
            "high": 300,
            "medium": 200,
            "routine": 400,
        }.get(priority, 200)
