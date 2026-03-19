"""Agent Brain — autonomous trading analysis and decision engine.

Runs in a Docker container with no network access.
Communicates with the trading system exclusively through MCP tools.
Uses Claude API (via MCP Proxy in the trusted domain) for reasoning.

Architecture:
  Agent Container (network: none)
    ↕ unix socket or shared volume
  MCP Proxy (trusted domain, has network)
    ↕ HTTPS
  Claude API / Exchange / Data Sources
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tools import ToolExecutor, get_tool_definitions, validate_tool_call
from agent.memory import AgentMemory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("agent.brain")

# Agent tier (0=observer, 1=advisor, 2=operator, 3=autonomous)
AGENT_TIER = int(os.environ.get("AGENT_TIER", "0"))

# Schedule intervals (seconds)
FULL_ANALYSIS_INTERVAL = 8 * 3600    # Every 8 hours
QUICK_CHECK_INTERVAL = 4 * 3600      # Every 4 hours
HEARTBEAT_INTERVAL = 300              # Every 5 minutes


def load_prompt(role: str) -> str:
    """Load a role-specific system prompt."""
    prompt_path = Path(__file__).parent / "prompts" / f"{role}.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return f"You are a {role} agent for a crypto futures trading system."


def call_claude(
    system_prompt: str,
    user_message: str,
    tools: list[dict] | None = None,
    model: str = "claude-haiku-4-5-20251001",
) -> dict:
    """Call Claude API with tool use support.

    In production, this is proxied through the MCP Proxy in the trusted domain.
    The agent container itself has no network access.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()  # Uses ANTHROPIC_API_KEY env var

        messages = [{"role": "user", "content": user_message}]
        kwargs = {
            "model": model,
            "max_tokens": 2048,
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = client.messages.create(**kwargs)
        return {
            "content": response.content,
            "stop_reason": response.stop_reason,
            "usage": {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        }
    except Exception as e:
        logger.error("Claude API call failed: %s", e)
        return {"error": str(e)}


class AgentBrain:
    """Main agent loop — analyzes markets and makes bounded decisions."""

    def __init__(self):
        self.executor = ToolExecutor()
        self.memory = AgentMemory()
        self.tier = AGENT_TIER
        self.tools = get_tool_definitions()
        self._last_full_analysis = 0
        self._last_quick_check = 0

    def run_forever(self) -> None:
        """Main loop — runs scheduled tasks."""
        logger.info("Agent Brain started. Tier=%d", self.tier)

        # Initial analysis on startup
        self.run_full_analysis()

        while True:
            now = time.time()

            if now - self._last_full_analysis >= FULL_ANALYSIS_INTERVAL:
                self.run_full_analysis()

            elif now - self._last_quick_check >= QUICK_CHECK_INTERVAL:
                self.run_quick_check()

            # Heartbeat
            logger.debug("Agent heartbeat. Tier=%d", self.tier)
            time.sleep(HEARTBEAT_INTERVAL)

    def run_full_analysis(self) -> None:
        """Full market analysis cycle (every 8 hours).

        Flow: Gather data → Analyze → Propose actions → Execute (if approved)
        """
        logger.info("=== Full Analysis Cycle ===")
        self._last_full_analysis = time.time()

        # 1. Gather all market data
        market = self.executor.execute("get_market_overview", {})
        regime = self.executor.execute("get_regime", {})
        positions = self.executor.execute("get_open_positions", {})

        # 2. Build analysis prompt
        prompt = self._build_analysis_prompt(market, regime, positions)

        # 3. Call Claude with tools
        system = load_prompt("analyst")
        response = call_claude(system, prompt, tools=self.tools)

        if "error" in response:
            logger.error("Analysis failed: %s", response["error"])
            return

        # 4. Process response — execute any tool calls
        self._process_response(response, context={
            "cycle": "full_analysis",
            "regime": regime.get("regime", "UNKNOWN"),
            "confidence": market.get("confidence", {}).get("score", 0),
        })

        self._last_quick_check = time.time()  # Reset quick check timer

    def run_quick_check(self) -> None:
        """Quick position/risk check (every 4 hours)."""
        logger.info("=== Quick Check ===")
        self._last_quick_check = time.time()

        positions = self.executor.execute("get_open_positions", {})
        regime = self.executor.execute("get_regime", {})

        if not positions.get("positions"):
            logger.info("No open positions. Quick check complete.")
            return

        prompt = f"""快速風險檢查:
持倉: {json.dumps(positions, ensure_ascii=False)}
市場機制: {json.dumps(regime, ensure_ascii=False)}
時間: {datetime.now(timezone.utc).isoformat()}

檢查:
1. 持倉是否需要緊急處理?
2. 是否需要觸發熔斷?
3. 有無建議調整?

如果一切正常，只需簡短確認即可。"""

        system = load_prompt("risk_manager")
        response = call_claude(system, prompt, tools=self.tools, model="claude-haiku-4-5-20251001")

        if "error" not in response:
            self._process_response(response, context={
                "cycle": "quick_check",
                "regime": regime.get("regime", "UNKNOWN"),
            })

    def _build_analysis_prompt(self, market: dict, regime: dict, positions: dict) -> str:
        """Build the full analysis prompt with all market data."""
        memory_stats = self.memory.get_stats()
        recent_decisions = self.memory.get_decisions(limit=5)

        return f"""完整市場分析請求
時間: {datetime.now(timezone.utc).isoformat()}
Agent Tier: {self.tier}

=== 市場數據 ===
{json.dumps(market, indent=2, ensure_ascii=False, default=str)[:3000]}

=== 市場機制 ===
{json.dumps(regime, indent=2, ensure_ascii=False)}

=== 持倉 ===
{json.dumps(positions, indent=2, ensure_ascii=False)}

=== 記憶統計 ===
{json.dumps(memory_stats, ensure_ascii=False)}

=== 最近決策 ===
{json.dumps(recent_decisions, indent=2, ensure_ascii=False, default=str)[:1000]}

請分析:
1. 當前市場環境評估 (用繁體中文)
2. 風險水位建議
3. 需要執行的行動 (使用可用 tools)
4. 3 個可能的情境劇本 (樂觀/基準/悲觀)

重要: 使用 log_decision 記錄你的分析結論。
{'如果需要調整風險/槓桿，使用對應的 tools。' if self.tier >= 1 else '目前為觀察者模式 (Tier 0)，只能讀取數據和記錄決策。'}"""

    def _process_response(self, response: dict, context: dict) -> None:
        """Process Claude's response, executing any tool calls."""
        for block in response.get("content", []):
            if hasattr(block, "type"):
                if block.type == "text":
                    logger.info("Agent: %s", block.text[:200])
                elif block.type == "tool_use":
                    self._handle_tool_call(block.name, block.input, context)

    def _handle_tool_call(self, name: str, args: dict, context: dict) -> dict:
        """Validate and execute a tool call."""
        # Validate
        rejection = validate_tool_call(name, args, self.tier)
        if rejection:
            logger.warning("Tool call REJECTED: %s — %s", name, rejection)
            # Log the rejection
            self.memory.log_decision(
                action=f"REJECTED:{name}",
                reason=rejection,
                confidence=0,
                context={"args": args, **context},
                regime=context.get("regime"),
            )
            # Alert on rejected control actions
            if name in ("set_leverage_cap", "pause_entries", "set_risk_level"):
                try:
                    from market_monitor.telegram_zh import send_message
                    send_message(f"🛡️ *Agent Tool 被拒*\n{name}: {rejection}")
                except Exception:
                    pass
            return {"error": rejection}

        # Execute
        logger.info("Executing tool: %s(%s)", name, json.dumps(args, ensure_ascii=False)[:100])
        result = self.executor.execute(name, args)
        logger.info("Tool result: %s", json.dumps(result, ensure_ascii=False)[:200])
        return result


# =============================================
# Rule-Based Analysis (no Claude API needed)
# =============================================

def run_rule_based_analysis() -> dict:
    """Run a pure rule-based analysis — no LLM required.

    Uses RegimeDetector + market data to generate a structured report
    and log a decision. Can run as a cron job without API key.
    """
    from agent.regime_detector import RegimeDetector

    executor = ToolExecutor()
    memory = AgentMemory()
    detector = RegimeDetector()

    logger.info("=== Rule-Based Analysis ===")

    # 1. Detect regime
    regime = detector.detect()
    logger.info("Regime: %s (confidence: %.0f%%)", regime["regime"], regime["confidence"] * 100)

    # 2. Get positions
    positions = executor.execute("get_open_positions", {})

    # 3. Build report
    guidance = regime.get("guidance", {})
    factors = regime.get("factors", {})

    report = {
        "regime": regime["regime"],
        "regime_confidence": regime["confidence"],
        "guidance": guidance,
        "factors": factors,
        "open_positions": len(positions.get("positions", [])),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 4. Log decision
    decision_id = memory.log_decision(
        action="rule_based_analysis",
        reason=f"Regime: {regime['regime']} — {guidance.get('description', '')}",
        confidence=regime["confidence"],
        context=report,
        regime=regime["regime"],
    )

    # 5. Send Telegram report
    try:
        from market_monitor.telegram_zh import send_message

        conf = factors.get("confidence", {})
        fg = factors.get("fear_greed", "?")
        btc_env = factors.get("btc_env", "?")

        conf_score = conf.get('score', '?')
        conf_regime = conf.get('regime', '?')
        pos_count = len(positions.get('positions', []))

        msg = (
            f"🤖 Agent 分析報告 (規則模式)\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"機制: {regime['regime']} ({regime['confidence']:.0%})\n"
            f"建議: {guidance.get('description', '')}\n"
            f"風險: {guidance.get('risk_level', '?')}\n"
            f"槓桿上限: {guidance.get('leverage_cap', '?')}x\n\n"
            f"信心: {conf_score} ({conf_regime})\n"
            f"F&G: {fg}\n"
            f"BTC Env: {btc_env}\n"
            f"持倉: {pos_count} 個\n\n"
            f"決策ID: {decision_id}"
        )

        send_message(msg, parse_mode="")
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)

    logger.info("Analysis complete. Decision ID: %s", decision_id)
    return report


# =============================================
# Entry Point
# =============================================

def main():
    mode = os.environ.get("AGENT_MODE", "loop")

    if mode == "rule":
        # Rule-based analysis (no Claude API needed)
        run_rule_based_analysis()
    elif mode == "once":
        brain = AgentBrain()
        brain.run_full_analysis()
    else:
        # Check if Claude API is available
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("No ANTHROPIC_API_KEY — falling back to rule-based mode")
            run_rule_based_analysis()
        else:
            brain = AgentBrain()
            brain.run_forever()


if __name__ == "__main__":
    main()
