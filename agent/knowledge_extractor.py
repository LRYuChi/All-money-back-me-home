"""知識提煉引擎 — 從決策歷史中提煉可操作規則。"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """你是量化交易知識提煉引擎。唯一任務：從數據中發現模式，不得推測或編造。

## 數據集
市場環境：{regime}
時間範圍：{start_date} 至 {end_date}
交易筆數：{n}（成功 {wins} 筆，失敗 {losses} 筆）

## 原始交易記錄
{decisions_json}

## 提煉步驟
1. 按「行動類型+結果」分組
2. 找成功/失敗交易的共同輸入條件
3. 只有滿足以下才能提煉為規則：
   - 支持案例 >= 3 筆
   - 勝率 >= 60% 或 <= 30%
   - 有明確觸發條件和預期結果

## 輸出（嚴格 JSON，無其他文字）
{{"rules":[{{"title":"規則標題(10字內)","condition":"觸發條件(引用具體指標)","expected_outcome":"預期結果","evidence_ids":["id1","id2","id3"],"evidence_count":3,"success_rate":0.75,"confidence":0.6,"domain":"risk|signal|regime|execution","counter_evidence_ids":[]}}],"insufficient_data_areas":["數據不足的領域"],"anomalies":["異常模式"]}}

## 禁止
- 無 evidence_ids 的規則
- confidence > 0.8（除非 evidence >= 10）
- condition 中使用「可能」「或許」"""


class KnowledgeExtractor:
    """從決策歷史中提煉可操作的交易規則。"""

    def __init__(self, memory):
        self.memory = memory

    def extract(self, regime: str = None, days: int = 30) -> dict:
        """從決策歷史提煉知識規則"""
        decisions = self.memory.get_decisions(limit=200)
        if not decisions:
            return {"rules": [], "insufficient_data_areas": ["無決策記錄"]}

        # 依市場環境篩選，並排除無結果的決策
        filtered = []
        for d in decisions:
            if isinstance(d, dict):
                if regime and d.get("regime") != regime:
                    continue
                if d.get("was_successful") is not None:
                    filtered.append(d)

        if len(filtered) < 5:
            return {"rules": [], "insufficient_data_areas": [f"有結果的決策不足（{len(filtered)}筆）"]}

        wins = sum(1 for d in filtered if d.get("was_successful"))
        losses = len(filtered) - wins

        # 組建提示詞
        prompt = EXTRACTION_PROMPT.format(
            regime=regime or "ALL",
            start_date=filtered[-1].get("timestamp", "?")[:10] if filtered else "?",
            end_date=filtered[0].get("timestamp", "?")[:10] if filtered else "?",
            n=len(filtered),
            wins=wins,
            losses=losses,
            decisions_json=json.dumps(filtered[:50], ensure_ascii=False, default=str)[:8000],
        )

        # 呼叫 Claude 進行提煉
        try:
            result = self._call_claude(prompt)
            if result:
                self._store_rules(result, regime)
                return result
        except Exception as e:
            logger.error("知識提煉失敗: %s", e)

        return {"rules": [], "insufficient_data_areas": ["Claude API 呼叫失敗"]}

    def _call_claude(self, prompt: str) -> dict | None:
        """呼叫 Claude 進行知識提煉"""
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # 從回應中擷取 JSON
            if "{" in text:
                start = text.index("{")
                depth = 0
                for i, c in enumerate(text[start:], start):
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    if depth == 0:
                        return json.loads(text[start:i + 1])
        except ImportError:
            logger.warning("anthropic SDK 未安裝，跳過知識提煉")
        except Exception as e:
            logger.error("Claude 呼叫失敗: %s", e)
        return None

    def _store_rules(self, result: dict, regime: str = None):
        """將提煉的規則存入記憶"""
        for rule in result.get("rules", []):
            if rule.get("evidence_count", 0) >= 3:
                self.memory.upsert_knowledge(
                    domain=rule.get("domain", "general"),
                    title=rule.get("title", "未命名"),
                    content=json.dumps({
                        "condition": rule.get("condition"),
                        "expected_outcome": rule.get("expected_outcome"),
                        "success_rate": rule.get("success_rate"),
                        "evidence_ids": rule.get("evidence_ids", []),
                    }, ensure_ascii=False),
                    regime=regime,
                    confidence=min(rule.get("confidence", 0.5), 0.8),
                    evidence_count=rule.get("evidence_count", 1),
                )
                logger.info(
                    "知識規則已存儲: %s (conf=%.2f)",
                    rule.get("title"),
                    rule.get("confidence", 0),
                )
