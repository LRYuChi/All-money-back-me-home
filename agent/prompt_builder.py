"""Prompt 壓縮器 — 同樣資訊，更少 token。"""

import json
import logging

logger = logging.getLogger(__name__)


class PromptBuilder:

    def compress_market_data(self, data: dict) -> str:
        """將市場數據壓縮為單行（約 40 tokens）。"""
        if not data:
            return "市場數據：無"
        parts = []
        # 信心分數
        conf = data.get("confidence", {})
        if isinstance(conf, dict):
            parts.append(f"信心:{conf.get('score', '?'):.2f}({conf.get('regime', '?')})")
        elif isinstance(conf, (int, float)):
            parts.append(f"信心:{conf:.2f}")
        # 加密貨幣環境
        crypto = data.get("crypto_env", {})
        if crypto:
            for sym in ["BTC", "ETH", "SOL"]:
                env = crypto.get(sym, {})
                if env:
                    score = env.get("score", "?")
                    parts.append(f"{sym}:{score}")
        # 總經指標
        macro = data.get("macro", {})
        if macro:
            for k in ["vix", "fear_greed"]:
                v = macro.get(k)
                if v is not None:
                    parts.append(f"{k}:{v}")
        return "市場:" + " ".join(parts) if parts else "市場：數據不足"

    def compress_performance(self, perf: dict) -> str:
        """將績效數據壓縮為關鍵指標行（約 60 tokens）。"""
        if not perf:
            return "績效：無數據"
        parts = []
        for key in ["win_rate", "profit_factor", "consecutive_losses", "total_trades"]:
            v = perf.get(key)
            if v is not None:
                label = {"win_rate": "WR", "profit_factor": "PF",
                         "consecutive_losses": "CL", "total_trades": "T"}.get(key, key)
                if isinstance(v, float):
                    parts.append(f"{label}:{v:.2f}")
                else:
                    parts.append(f"{label}:{v}")
        return "績效:" + " ".join(parts) if parts else "績效：無"

    def compress_memory(self, memories: list) -> str:
        """將記憶壓縮為標題+信心索引（每筆約 15 tokens）。"""
        if not memories:
            return "知識：無"
        lines = []
        for m in memories[:5]:
            mid = m.get("id", "?")[:8] if isinstance(m, dict) else "?"
            title = m.get("title", "?") if isinstance(m, dict) else str(m)[:30]
            conf = m.get("confidence", "?") if isinstance(m, dict) else "?"
            regime = m.get("regime", "") if isinstance(m, dict) else ""
            lines.append(f"[{mid}] {title} (c:{conf}" + (f",r:{regime}" if regime else "") + ")")
        return "知識:\n" + "\n".join(lines)

    def build_prompt(
        self,
        skill_context: str,
        state: dict,
        perf: dict,
        market: dict,
        memories: list,
        trigger_reason: str,
        priority: str,
        all_reasons: list[str] | None = None,
    ) -> str:
        """組合壓縮 prompt：skills + 數據。"""
        data_section = "\n".join([
            self.compress_market_data(market),
            self.compress_performance(perf),
            self.compress_memory(memories),
            f"觸發:{trigger_reason}" + (f" (+{'|'.join(all_reasons[1:])})" if all_reasons and len(all_reasons) > 1 else ""),
            f"優先:{priority}",
        ])
        return f"{skill_context}\n\n## 當前數據\n{data_section}"

    @staticmethod
    def parse_minimal_output(raw: str) -> dict:
        """解析 Claude 的最小化 JSON 輸出。"""
        text = raw.strip()
        # 嘗試從回應中擷取 JSON
        if "{" in text:
            start = text.index("{")
            # 找到對應的右大括號
            depth = 0
            for i, c in enumerate(text[start:], start):
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    text = text[start:i+1]
                    break
        try:
            minimal = json.loads(text)
            # 展開縮寫鍵值
            return {
                "action": minimal.get("a", minimal.get("action", "no_action")),
                "changes": minimal.get("c", minimal.get("changes", {})),
                "reason": minimal.get("r", minimal.get("reason", "")),
                "confidence": minimal.get("conf", minimal.get("confidence", 0.5)),
                "requires_human": minimal.get("h", minimal.get("requires_human", False)),
                "data_citations": minimal.get("cite", minimal.get("data_citations", [])),
            }
        except json.JSONDecodeError:
            logger.warning("無法解析 Claude 輸出: %s", text[:200])
            return {
                "action": "no_action",
                "changes": {},
                "reason": f"輸出解析失敗: {text[:50]}",
                "confidence": 0.0,
                "requires_human": True,
                "data_citations": [],
            }
