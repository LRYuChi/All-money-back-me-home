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

# P0/P1 模組整合
try:
    from agent.skill_loader import SkillLoader
    from agent.trigger_engine import TriggerEngine
    from agent.prompt_builder import PromptBuilder
    from agent.hallucination_guard import HallucinationGuard
    from agent.observability import Observability
    from agent.model_router import ModelRouter
    from agent.cache_layer import AgentCache
    from agent.token_metrics import TokenMetrics
    _SKILLS_AVAILABLE = True
except ImportError as e:
    _SKILLS_AVAILABLE = False
    import logging as _logging
    _logging.getLogger(__name__).warning("Skills 模組載入失敗: %s, 使用原有模式", e)

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
    model: str = "claude-sonnet-4-6",
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

        # Skills architecture (P0/P1)
        if _SKILLS_AVAILABLE:
            self.skill_loader = SkillLoader()
            self.trigger_engine = TriggerEngine()
            self.prompt_builder = PromptBuilder()
            self.hallucination_guard = HallucinationGuard()
            self.observability = Observability()
            self.model_router = ModelRouter()
            self.cache = AgentCache(ttl_seconds=900)
            self.token_metrics = TokenMetrics()
            logger.info("Skills 架構已載入")
        else:
            self.skill_loader = None
            self.trigger_engine = None

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

        # 嘗試 Skills 架構（優先）
        if _SKILLS_AVAILABLE and self.skill_loader:
            try:
                lightweight_state = self._get_lightweight_state()
                skills_result = self.run_skills_cycle(lightweight_state)
                if skills_result is not None:
                    # Skills 架構產生了有效決策，執行它
                    self._execute_decision(skills_result)
                    return
                # skills_result is None = 無觸發或 no_action，繼續原有流程
            except Exception as e:
                logger.warning("Skills 週期失敗，降級至原有模式: %s", e)

        # 1. Gather all market data
        market = self.executor.execute("get_market_overview", {})
        regime = self.executor.execute("get_regime", {})
        positions = self.executor.execute("get_open_positions", {})

        # 2. Build analysis prompt
        prompt = self._build_analysis_prompt(market, regime, positions)

        # 3. Call Claude with tools — multi-turn loop
        system = load_prompt("analyst")
        context = {
            "cycle": "full_analysis",
            "regime": regime.get("regime", "UNKNOWN"),
            "confidence": market.get("confidence", {}).get("score", 0),
        }

        result = self._run_agent_loop(system, prompt, context, max_turns=5)
        if result is None:
            logger.error("AI analysis failed — falling back to rule-based")
            run_rule_based_analysis()
            return

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
        response = call_claude(system, prompt, tools=self.tools, model="claude-sonnet-4-6")

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

    def _run_agent_loop(self, system: str, user_prompt: str, context: dict,
                        max_turns: int = 5, model: str = "claude-sonnet-4-6") -> str | None:
        """Run a multi-turn agent loop with tool use.

        Claude calls tools → we execute → send results back → Claude continues.
        Repeats until Claude stops calling tools or max_turns reached.
        """
        import anthropic

        try:
            client = anthropic.Anthropic()
        except Exception as e:
            logger.error("Anthropic client init failed: %s", e)
            return None

        messages = [{"role": "user", "content": user_prompt}]
        final_text = ""

        for turn in range(max_turns):
            try:
                response = client.messages.create(
                    model=model,
                    max_tokens=2048,
                    system=system,
                    messages=messages,
                    tools=self.tools,
                )
            except Exception as e:
                logger.error("Claude API call failed (turn %d): %s", turn, e)
                return None

            logger.info("Turn %d: stop_reason=%s, blocks=%d",
                        turn, response.stop_reason, len(response.content))

            # Process response blocks
            tool_results = []
            for block in response.content:
                if block.type == "text":
                    logger.info("Agent: %s", block.text[:300])
                    final_text += block.text + "\n"
                elif block.type == "tool_use":
                    result = self._handle_tool_call(block.name, block.input, context)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False, default=str)[:3000],
                    })

            # If no more tool calls, we're done
            if response.stop_reason == "end_turn" or not tool_results:
                break

            # Send tool results back to Claude for next turn
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})

        return final_text or "Analysis completed (tools only)"

    def run_skills_cycle(self, state: dict) -> dict | None:
        """
        Skills 架構的決策週期（取代原有的固定 prompt 模式）。
        流程: 觸發判斷 → 快取 → Skill 選擇 → 壓縮 Prompt → Claude → 驗證 → 執行
        """
        if not _SKILLS_AVAILABLE:
            return None  # 降級到原有模式

        # Step 1: 觸發判斷（0 token）
        should_call, reason, priority, all_reasons = self.trigger_engine.should_invoke_claude(state)

        if not should_call:
            self.token_metrics.record(event="skipped", trigger_reason=reason)
            return None

        # Step 2: 快取查詢（0 token）
        cached = self.cache.get(state)
        if cached:
            self.token_metrics.record(event="cache_hit", trigger_reason=reason)
            if cached.get("action") != "no_action":
                return cached
            return None

        # Step 3: Skill 載入（三層降級）
        skill_context, load_method = self.skill_loader.load_with_fallback(
            trigger_reason=reason, priority=priority,
            regime=state.get("regime", "UNKNOWN"),
            token_budget={"critical": 1500, "high": 1000, "medium": 600, "routine": 2000}.get(priority, 800)
        )

        # Step 4: 幻覺防護 — 輸入層
        grounding = self.hallucination_guard.build_grounded_prompt(state)

        # Step 5: 壓縮 Prompt
        perf = state.get("performance", {})
        market = state.get("market", {})
        memories = []
        if hasattr(self, 'memory') and self.memory and priority in ("routine", "high", "critical"):
            try:
                memories = self.memory.retrieve_relevant(
                    regime=state.get("regime", "UNKNOWN"),
                    limit=5
                )
            except Exception:
                pass

        prompt = self.prompt_builder.build_prompt(
            skill_context=skill_context,
            state=state, perf=perf, market=market,
            memories=memories, trigger_reason=reason,
            priority=priority, all_reasons=all_reasons,
        )
        prompt = grounding + "\n\n" + prompt

        # Step 6: 選擇模型
        model = self.model_router.get_model("decision_normal", priority)
        max_output = self.model_router.get_max_output_tokens(priority)

        # Step 7: 呼叫 Claude
        try:
            import anthropic
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=model,
                max_tokens=max_output,
                messages=[{"role": "user", "content": prompt}],
            )
            # R121: filter to TextBlock (extended thinking 防呆 — content[0]
            # 可能是 ThinkingBlock 沒 .text attribute)
            _text_blocks = [b for b in (response.content or []) if hasattr(b, "text")]
            raw_text = _text_blocks[0].text if _text_blocks else ""
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        except Exception as e:
            logger.error("Claude API 呼叫失敗: %s", e)
            self.token_metrics.record(event="api_error", trigger_reason=reason)
            return None

        # Step 8: 解析輸出
        decision = self.prompt_builder.parse_minimal_output(raw_text)

        # Step 9: 幻覺防護 — 輸出層
        valid, errors = self.hallucination_guard.validate_decision(decision, state)
        if not valid:
            logger.warning("決策驗證失敗: %s", errors)
            decision = {"action": "no_action", "reason": f"驗證失敗: {'; '.join(errors)}", "confidence": 0.0}

        # Step 10: 快取 + Token 記錄
        self.cache.set(state, decision)
        cost = self.model_router.estimate_cost(model, input_tokens, output_tokens)
        self.token_metrics.record(
            event="api_call", tokens=input_tokens + output_tokens,
            cost_usd=cost, model=model, priority=priority,
            action=decision.get("action", ""), trigger_reason=reason,
            skills_used=self.skill_loader.select_skills(reason, priority, state.get("regime", "UNKNOWN")),
        )

        # Step 11: 可觀測性追蹤
        self.observability.emit_trace(
            inputs={"regime": state.get("regime"), "trigger": reason, "data_quality": grounding[:100]},
            reasoning={"model": model, "prompt_tokens": input_tokens, "output_tokens": output_tokens},
            output={"decision": decision, "validation": {"valid": valid, "errors": errors}},
            skills_used=self.skill_loader.select_skills(reason, priority, state.get("regime", "UNKNOWN")),
            tokens_input=input_tokens, tokens_output=output_tokens,
            cost_usd=cost, model=model, priority=priority,
            trigger_reason=reason, load_method=load_method,
        )

        # Step 12: 排程決策後驗證
        if decision.get("action") != "no_action":
            try:
                self.hallucination_guard.schedule_verification(
                    decision_id=f"skills_{int(time.time())}",
                    decision=decision,
                    current_metrics=perf,
                )
            except Exception:
                pass

        logger.info(
            "Skills 決策: action=%s conf=%.2f model=%s tokens=%d cost=$%.4f trigger=%s",
            decision.get("action"), decision.get("confidence", 0),
            model, input_tokens + output_tokens, cost, reason,
        )

        return decision if decision.get("action") != "no_action" else None

    def _get_lightweight_state(self) -> dict:
        """取得輕量狀態快照（不呼叫 API，只讀本地數據）"""
        state = {}
        try:
            from market_monitor.state_store import BotStateStore
            bot_state = BotStateStore.read()
            state.update({
                "regime": bot_state.get("last_confidence_regime", "UNKNOWN"),
                "confidence_score": bot_state.get("last_confidence_score", 0.5),
                "consecutive_losses": bot_state.get("consecutive_losses", 0),
                "guard_rejections": bot_state.get("guard_rejections_today", 0),
                "agent_risk_level": bot_state.get("agent_risk_level"),
            })
            # Crypto env
            crypto = bot_state.get("crypto_env_cache", {})
            state["crypto_env"] = crypto
        except Exception:
            pass

        # Performance from memory
        try:
            if hasattr(self, 'memory') and self.memory:
                stats = self.memory.get_stats()
                state["performance"] = stats
                state["last_routine_analysis"] = stats.get("last_analysis_time")
        except Exception:
            pass

        return state

    def _execute_decision(self, decision: dict):
        """執行 Skills 架構的決策"""
        action = decision.get("action", "no_action")
        changes = decision.get("changes", {})
        reason = decision.get("reason", "")

        if action == "no_action":
            return

        try:
            from agent.tools import execute_tool
            result = execute_tool(action, changes)
            logger.info("Skills 決策已執行: %s → %s", action, result)
        except ImportError:
            # Fallback: direct state store update
            from market_monitor.state_store import BotStateStore
            if action == "pause_bot":
                BotStateStore.update(agent_pause_entries=True)
            elif action == "adjust_risk":
                level = changes.get("risk_level", changes.get("level", "conservative"))
                BotStateStore.update(agent_risk_level=level)
            elif action == "adjust_params":
                if "leverage_cap" in changes:
                    BotStateStore.update(agent_leverage_cap=changes["leverage_cap"])
            elif action == "send_alert":
                from market_monitor.telegram_zh import send_message
                send_message(f"🤖 Agent: {reason}")
            logger.info("Skills 決策已執行(fallback): %s", action)

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
# Pipeline Mode: Data → Summary → AI (最省 token)
# =============================================

def run_pipeline_analysis() -> dict | None:
    """三步驟 Pipeline 分析 — 最省 token。

    Step 1: data_collector.py 已由 shell script 預先執行
    Step 2: summarizer.py 已由 shell script 預先執行
    Step 3: 讀取摘要 → Claude 單次分析 (無 tool_use)

    Token 用量: ~2500 (vs 舊架構 ~10000)
    """
    summary_path = Path(os.environ.get("DATA_DIR", "/app/data")) / "analysis_input.txt"

    # 如果摘要不存在，先執行 Step 1+2
    if not summary_path.exists():
        logger.info("No summary found, running data collection + summarization...")
        try:
            from agent.data_collector import collect_all
            from agent.summarizer import run as run_summarizer
            collect_all()
            run_summarizer()
        except Exception as e:
            logger.error("Pipeline data collection failed: %s", e)
            return None

    if not summary_path.exists():
        logger.error("Summary file still missing after collection")
        return None

    summary = summary_path.read_text()
    logger.info("Summary loaded: %d chars", len(summary))

    # 單次 Claude 調用 — 無 tools，最省 token
    system = load_prompt("analyst")
    prompt = f"""根據以下市場快照進行分析。用繁體中文回答。

{summary}

請輸出（簡潔扼要）:
1. 環境評估 (1-2 句)
2. 風險建議: conservative / normal / aggressive
3. 建議行動 (具體)
4. 三情境 (樂觀/基準/悲觀，各 1 句)"""

    response = call_claude(
        system_prompt=system,
        user_message=prompt,
        tools=None,  # 不傳 tools → 省 ~1500 tokens
        model="claude-sonnet-4-6",
    )

    if "error" in response:
        logger.error("Pipeline AI analysis failed: %s — falling back to rule-based", response["error"])
        run_rule_based_analysis()
        return None

    # 提取回應文字
    analysis_text = ""
    for block in response.get("content", []):
        if hasattr(block, "type") and block.type == "text":
            analysis_text += block.text

    if not analysis_text:
        logger.warning("Empty AI response, falling back to rule-based")
        run_rule_based_analysis()
        return None

    logger.info("AI Analysis: %s", analysis_text[:300])

    # 記錄決策
    memory = AgentMemory()
    decision_id = memory.log_decision(
        action="pipeline_analysis",
        reason=analysis_text[:500],
        confidence=0.85,
        context={"mode": "pipeline", "summary_chars": len(summary)},
    )

    # 發送 Telegram
    try:
        from market_monitor.telegram_zh import send_message
        tokens_used = response.get("usage", {})
        token_info = f"tokens: {tokens_used.get('input_tokens', '?')}in/{tokens_used.get('output_tokens', '?')}out"

        msg = (
            f"🤖 Agent 分析報告 (Pipeline)\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"{analysis_text[:800]}\n\n"
            f"ID: {decision_id} | {token_info}"
        )
        send_message(msg, parse_mode="")
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)

    logger.info("Pipeline analysis complete. ID: %s, tokens: %s",
                decision_id, response.get("usage", {}))
    return {"analysis": analysis_text, "decision_id": decision_id}


# =============================================
# Entry Point
# =============================================

def main():
    mode = os.environ.get("AGENT_MODE", "loop")

    if mode == "pipeline":
        # New pipeline mode (data → summary → AI, minimal tokens)
        run_pipeline_analysis()
    elif mode == "rule":
        run_rule_based_analysis()
    elif mode == "once":
        # Legacy: full tool_use mode
        brain = AgentBrain()
        brain.run_full_analysis()
    else:
        # Default: pipeline if API key available, else rule-based
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            run_pipeline_analysis()
        else:
            logger.warning("No ANTHROPIC_API_KEY — rule-based mode")
            run_rule_based_analysis()


if __name__ == "__main__":
    main()
