"""Tests for the Agent framework — tools, memory, and security boundaries."""

import os
import tempfile


from agent.tools import validate_tool_call, get_tool_definitions, _last_tool_call
from agent.memory import AgentMemory


# =============================================
# Tool Validation Tests
# =============================================

class TestToolValidation:
    def test_tier_0_read_allowed(self):
        assert validate_tool_call("get_market_overview", {}, tier=0) is None

    def test_tier_0_control_blocked(self):
        result = validate_tool_call("set_risk_level", {"level": "conservative", "reason": "test"}, tier=0)
        assert result is not None
        assert "Tier 1" in result

    def test_tier_1_control_allowed(self):
        # Clear cooldown
        _last_tool_call.pop("set_risk_level", None)
        assert validate_tool_call("set_risk_level", {"level": "conservative", "reason": "test"}, tier=1) is None

    def test_tier_1_pause_blocked(self):
        result = validate_tool_call("pause_entries", {"hours": 4, "reason": "test"}, tier=1)
        assert result is not None
        assert "Tier 2" in result

    def test_tier_2_pause_allowed(self):
        _last_tool_call.pop("pause_entries", None)
        assert validate_tool_call("pause_entries", {"hours": 4, "reason": "test"}, tier=2) is None

    def test_leverage_cap_out_of_range(self):
        _last_tool_call.pop("set_leverage_cap", None)
        result = validate_tool_call("set_leverage_cap", {"max_leverage": 10.0, "reason": "test"}, tier=1)
        assert result is not None
        assert "out of range" in result

    def test_leverage_cap_in_range(self):
        _last_tool_call.pop("set_leverage_cap", None)
        assert validate_tool_call("set_leverage_cap", {"max_leverage": 3.0, "reason": "test"}, tier=1) is None

    def test_pause_hours_out_of_range(self):
        _last_tool_call.pop("pause_entries", None)
        result = validate_tool_call("pause_entries", {"hours": 48, "reason": "test"}, tier=2)
        assert result is not None
        assert "out of range" in result

    def test_log_decision_always_allowed(self):
        assert validate_tool_call("log_decision", {
            "action": "test",
            "reason": "test",
            "confidence": 0.5,
        }, tier=0) is None


class TestToolDefinitions:
    def test_definitions_not_empty(self):
        defs = get_tool_definitions()
        assert len(defs) > 0

    def test_all_have_name_and_schema(self):
        for tool in get_tool_definitions():
            assert "name" in tool
            assert "input_schema" in tool
            assert "description" in tool

    def test_control_tools_have_reason_field(self):
        control_tools = {"set_risk_level", "set_leverage_cap", "pause_entries"}
        for tool in get_tool_definitions():
            if tool["name"] in control_tools:
                props = tool["input_schema"].get("properties", {})
                assert "reason" in props, f"{tool['name']} missing 'reason' field"


# =============================================
# Memory Tests
# =============================================

class TestAgentMemory:
    def setup_method(self):
        self.tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmpfile.close()
        self.memory = AgentMemory(db_path=self.tmpfile.name)

    def teardown_method(self):
        os.unlink(self.tmpfile.name)

    def test_log_decision(self):
        did = self.memory.log_decision(
            action="test_action",
            reason="testing",
            confidence=0.8,
            context={"key": "value"},
            regime="TRENDING_BULL",
        )
        assert did is not None
        assert len(did) == 8

    def test_get_decisions(self):
        self.memory.log_decision("a1", "r1", 0.5)
        self.memory.log_decision("a2", "r2", 0.7)
        decisions = self.memory.get_decisions(limit=10)
        assert len(decisions) == 2
        assert decisions[0]["action"] == "a2"  # Most recent first

    def test_get_decisions_by_regime(self):
        self.memory.log_decision("a1", "r1", 0.5, regime="BULL")
        self.memory.log_decision("a2", "r2", 0.7, regime="BEAR")
        bull = self.memory.get_decisions(regime="BULL")
        assert len(bull) == 1
        assert bull[0]["action"] == "a1"

    def test_update_outcome(self):
        did = self.memory.log_decision("a1", "r1", 0.5)
        self.memory.update_outcome(did, outcome_7d="positive", was_successful=True)
        decisions = self.memory.get_decisions()
        assert decisions[0]["outcome_7d"] == "positive"
        assert decisions[0]["was_successful"] is True

    def test_knowledge_store(self):
        kid = self.memory.add_knowledge(
            category="pattern",
            content={"rule": "In BEAR regime, conservative risk works better"},
            regime="TRENDING_BEAR",
        )
        assert kid is not None

        knowledge = self.memory.get_knowledge(regime="TRENDING_BEAR")
        assert len(knowledge) == 1
        assert knowledge[0]["content"]["rule"].startswith("In BEAR")

    def test_stats(self):
        self.memory.log_decision("a1", "r1", 0.5)
        self.memory.log_decision("a2", "r2", 0.7)
        self.memory.add_knowledge("cat", {"test": True})

        stats = self.memory.get_stats()
        assert stats["total_decisions"] == 2
        assert stats["knowledge_entries"] == 1
