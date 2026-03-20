"""Agent Tool Router — secure, bounded tool execution for AI agents.

Every tool has hard parameter boundaries that cannot be exceeded.
Tool calls are logged to the decision memory for audit trail.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# =============================================
# Tool Constraints (hard boundaries)
# =============================================

TOOL_CONSTRAINTS = {
    "set_risk_level": {
        "type": "enum",
        "allowed": ["conservative", "normal", "aggressive"],
    },
    "set_leverage_cap": {
        "type": "range",
        "min": 1.0,
        "max": 5.0,
    },
    "pause_entries": {
        "type": "range",
        "min": 1,
        "max": 24,  # hours
    },
    "send_alert": {
        "type": "enum_field",
        "field": "urgency",
        "allowed": ["info", "warning", "critical"],
    },
}

# Cooldown: same control tool can only run once per period
TOOL_COOLDOWNS = {
    "set_risk_level": 3600,     # 1 hour
    "set_leverage_cap": 3600,
    "pause_entries": 3600,
}

_last_tool_call: dict[str, float] = {}


# =============================================
# Tool Definitions
# =============================================

def get_tool_definitions() -> list[dict]:
    """Return tool definitions for Claude API tool_use."""
    return [
        # === Perception Tools (Tier 0 — read-only) ===
        {
            "name": "get_market_overview",
            "description": "取得全市場數據快照：信心引擎分數、Crypto 環境、宏觀指標、Fear&Greed、BTC.D",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_confidence_score",
            "description": "取得信心引擎詳細分數：4 沙盒 + 事件乘數 + 制度 + 建議倉位/槓桿",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_crypto_environment",
            "description": "取得加密環境引擎分數：衍生品/鏈上/情緒三維評估 (BTC/ETH/SOL/BNB/XRP/DOGE)",
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "幣種 (BTC/ETH/SOL/BNB/XRP/DOGE)", "default": "BTC"},
                },
            },
        },
        {
            "name": "get_performance_metrics",
            "description": "取得交易績效：勝率、PF、回撤、連勝/連敗、總損益",
            "input_schema": {
                "type": "object",
                "properties": {
                    "lookback_days": {"type": "integer", "description": "回看天數", "default": 30},
                },
            },
        },
        {
            "name": "get_open_positions",
            "description": "取得當前所有持倉：幣種、方向、槓桿、損益",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "get_recent_trades",
            "description": "取得最近 N 筆已關閉交易",
            "input_schema": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "筆數", "default": 10},
                },
            },
        },
        {
            "name": "get_regime",
            "description": "取得當前市場機制判斷：TRENDING_BULL/BEAR, HIGH_VOLATILITY, ACCUMULATION, RANGING",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        # === Control Tools (Tier 1+ — bounded) ===
        {
            "name": "set_risk_level",
            "description": "調整風險水位 (conservative/normal/aggressive)。影響倉位大小。需 Tier 1+，1h 冷卻。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["conservative", "normal", "aggressive"]},
                    "reason": {"type": "string", "description": "調整原因"},
                },
                "required": ["level", "reason"],
            },
        },
        {
            "name": "set_leverage_cap",
            "description": "設定槓桿上限 (1.0-5.0x)。需 Tier 1+，1h 冷卻。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "max_leverage": {"type": "number", "minimum": 1.0, "maximum": 5.0},
                    "reason": {"type": "string"},
                },
                "required": ["max_leverage", "reason"],
            },
        },
        {
            "name": "pause_entries",
            "description": "暫停新進場 (1-24 小時)。需 Tier 2+，1h 冷卻。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "minimum": 1, "maximum": 24},
                    "reason": {"type": "string"},
                },
                "required": ["hours", "reason"],
            },
        },
        {
            "name": "send_alert",
            "description": "發送 Telegram 告警通知。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "maxLength": 500},
                    "urgency": {"type": "string", "enum": ["info", "warning", "critical"]},
                },
                "required": ["message", "urgency"],
            },
        },
        # === Decision Logging ===
        {
            "name": "log_decision",
            "description": "記錄一個 Agent 決策到記憶庫。所有重要判斷都應記錄。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "執行的動作"},
                    "reason": {"type": "string", "description": "決策理由"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "context": {"type": "object", "description": "決策時的市場上下文"},
                },
                "required": ["action", "reason", "confidence"],
            },
        },
    ]


# =============================================
# Tool Execution with Safety Checks
# =============================================

def validate_tool_call(name: str, args: dict, tier: int = 0) -> str | None:
    """Validate a tool call against constraints and tier.

    Returns None if valid, or error message string if rejected.
    """
    # Tier check
    tier_requirements = {
        "set_risk_level": 1,
        "set_leverage_cap": 1,
        "pause_entries": 2,
    }
    required_tier = tier_requirements.get(name, 0)
    if tier < required_tier:
        return f"Tool '{name}' requires Tier {required_tier}, current Tier is {tier}"

    # Cooldown check
    if name in TOOL_COOLDOWNS:
        last = _last_tool_call.get(name, 0)
        elapsed = time.time() - last
        if elapsed < TOOL_COOLDOWNS[name]:
            remaining = int(TOOL_COOLDOWNS[name] - elapsed)
            return f"Tool '{name}' on cooldown: {remaining}s remaining"

    # Constraint check
    constraint = TOOL_CONSTRAINTS.get(name)
    if constraint:
        if constraint["type"] == "enum":
            value = args.get("level") or args.get("urgency")
            if value not in constraint["allowed"]:
                return f"Invalid value '{value}' for '{name}'. Allowed: {constraint['allowed']}"

        elif constraint["type"] == "range":
            # Find the numeric arg
            for key in ("max_leverage", "hours", "max"):
                if key in args:
                    val = args[key]
                    if val < constraint["min"] or val > constraint["max"]:
                        return f"Value {val} out of range [{constraint['min']}, {constraint['max']}] for '{name}'"
                    break

    return None  # Valid


def record_tool_use(name: str) -> None:
    """Record that a control tool was used (for cooldown tracking)."""
    if name in TOOL_COOLDOWNS:
        _last_tool_call[name] = time.time()


# =============================================
# Tool Executor
# =============================================

class ToolExecutor:
    """Executes validated tool calls against the trading system."""

    def __init__(self, state_store_path: str = "/data/agent_state.json"):
        self._state_path = state_store_path

    def execute(self, name: str, args: dict) -> dict[str, Any]:
        """Execute a tool and return the result."""
        handler = getattr(self, f"_exec_{name}", None)
        if handler is None:
            return {"error": f"Unknown tool: {name}"}

        try:
            result = handler(args)
            record_tool_use(name)
            return result
        except Exception as e:
            logger.error("Tool execution failed: %s(%s) — %s", name, args, e)
            return {"error": str(e)}

    # --- Perception tools ---

    def _exec_get_market_overview(self, args: dict) -> dict:
        """Fetch market overview from dashboard API."""
        import urllib.request
        try:
            url = "http://api:8000/api/dashboard"
            with urllib.request.urlopen(url, timeout=30) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": f"Dashboard fetch failed: {e}"}

    def _exec_get_confidence_score(self, args: dict) -> dict:
        from market_monitor.confidence_engine import GlobalConfidenceEngine
        engine = GlobalConfidenceEngine()
        return engine.calculate()

    def _exec_get_crypto_environment(self, args: dict) -> dict:
        from market_monitor.crypto_environment import CryptoEnvironmentEngine
        import os
        cg_key = os.environ.get("COINGLASS_API_KEY")
        engine = CryptoEnvironmentEngine(coinglass_api_key=cg_key)
        symbol = args.get("symbol", "BTC")
        return engine.calculate(symbol)

    def _exec_get_performance_metrics(self, args: dict) -> dict:
        """Get trading performance from Freqtrade API."""
        return self._ft_api("profit") or {"error": "Freqtrade unavailable"}

    def _exec_get_open_positions(self, args: dict) -> dict:
        result = self._ft_api("status")
        return {"positions": result if isinstance(result, list) else []}

    def _exec_get_recent_trades(self, args: dict) -> dict:
        limit = args.get("limit", 10)
        result = self._ft_api(f"trades?limit={limit}")
        return {"trades": result.get("trades", []) if result else []}

    def _exec_get_regime(self, args: dict) -> dict:
        """Simple regime detection from available data."""
        try:
            dashboard = self._exec_get_market_overview({})
            if "error" in dashboard:
                return {"regime": "UNKNOWN", "reason": "data unavailable"}

            confidence = dashboard.get("confidence", {}).get("score", 0.5)
            regime = dashboard.get("confidence", {}).get("regime", "CAUTIOUS")

            # Crypto env
            crypto_env = dashboard.get("crypto_env", {})
            btc_env = crypto_env.get("BTC", {}).get("score", 0.5) if crypto_env else 0.5

            # VIX
            vix = dashboard.get("macro", {}).get("vix", {}).get("price", 20)

            # Simple regime mapping
            if confidence >= 0.7 and btc_env >= 0.6:
                detected = "TRENDING_BULL"
            elif confidence < 0.2:
                detected = "TRENDING_BEAR"
            elif vix > 30:
                detected = "HIGH_VOLATILITY"
            elif confidence >= 0.4 and btc_env >= 0.5:
                detected = "RANGING"
            else:
                detected = "ACCUMULATION"

            return {
                "regime": detected,
                "confidence_score": confidence,
                "confidence_regime": regime,
                "btc_env": btc_env,
                "vix": vix,
            }
        except Exception as e:
            return {"regime": "UNKNOWN", "error": str(e)}

    # --- Control tools ---

    def _exec_set_risk_level(self, args: dict) -> dict:
        level = args["level"]
        self._update_state({"agent_risk_level": level})
        return {"status": "ok", "risk_level": level}

    def _exec_set_leverage_cap(self, args: dict) -> dict:
        cap = args["max_leverage"]
        self._update_state({"agent_leverage_cap": cap})
        return {"status": "ok", "leverage_cap": cap}

    def _exec_pause_entries(self, args: dict) -> dict:
        hours = args["hours"]
        resume_at = time.time() + hours * 3600
        self._update_state({"agent_pause_entries": True, "agent_resume_at": resume_at})
        return {"status": "ok", "paused_hours": hours}

    def _exec_send_alert(self, args: dict) -> dict:
        message = args["message"][:500]
        urgency = args.get("urgency", "info")
        icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(urgency, "📢")
        try:
            from market_monitor.telegram_zh import send_message
            send_message(f"{icon} *Agent 告警*\n{message}")
            return {"status": "sent"}
        except Exception as e:
            return {"error": str(e)}

    # --- Decision logging ---

    def _exec_log_decision(self, args: dict) -> dict:
        from agent.memory import AgentMemory
        memory = AgentMemory()
        decision_id = memory.log_decision(
            action=args["action"],
            reason=args["reason"],
            confidence=args.get("confidence", 0.5),
            context=args.get("context", {}),
        )
        return {"status": "logged", "decision_id": decision_id}

    # --- Helpers ---

    def _ft_api(self, endpoint: str) -> dict | None:
        import base64
        import urllib.request
        try:
            _ft_creds = f"{os.environ.get('FT_USER', 'freqtrade')}:{os.environ.get('FT_PASS', 'freqtrade')}"
            auth = base64.b64encode(_ft_creds.encode()).decode()
            req = urllib.request.Request(
                f"http://freqtrade:8080/api/v1/{endpoint}",
                headers={"Authorization": f"Basic {auth}"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def _update_state(self, updates: dict) -> None:
        """Update agent state file (read by Freqtrade strategy)."""
        try:
            state = {}
            try:
                with open(self._state_path) as f:
                    state = json.load(f)
            except FileNotFoundError:
                pass
            state.update(updates)
            state["last_updated"] = time.time()
            with open(self._state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            logger.error("State update failed: %s", e)
