"""Tests for P1 strategy optimizations: partial schedules, guard layers."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "strategies"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "guards"))


class TestPartialSchedules:
    """Test regime-aware partial take-profit schedules."""

    def test_all_regimes_have_schedules(self):
        from strategies.smc_trend import _PARTIAL_SCHEDULES
        for regime in ["AGGRESSIVE", "NORMAL", "CAUTIOUS", "DEFENSIVE", "HIBERNATE"]:
            assert regime in _PARTIAL_SCHEDULES, f"Missing schedule for {regime}"

    def test_cautious_schedule(self):
        from strategies.smc_trend import _PARTIAL_SCHEDULES
        schedule = _PARTIAL_SCHEDULES["CAUTIOUS"]
        assert schedule == [(0.3, 0.25), (0.6, 0.25)]

    def test_aggressive_keeps_more(self):
        """AGGRESSIVE should sell less total than CAUTIOUS."""
        from strategies.smc_trend import _PARTIAL_SCHEDULES
        agg = _PARTIAL_SCHEDULES["AGGRESSIVE"]
        cau = _PARTIAL_SCHEDULES["CAUTIOUS"]
        agg_total = sum(f for _, f in agg)
        cau_total = sum(f for _, f in cau)
        assert agg_total < cau_total

    def test_defensive_exits_more(self):
        """DEFENSIVE should sell more total than CAUTIOUS (earlier profit locking)."""
        from strategies.smc_trend import _PARTIAL_SCHEDULES
        dfn = _PARTIAL_SCHEDULES["DEFENSIVE"]
        cau = _PARTIAL_SCHEDULES["CAUTIOUS"]
        dfn_total = sum(f for _, f in dfn)
        cau_total = sum(f for _, f in cau)
        assert dfn_total > cau_total

    def test_schedule_fractions_sum_under_one(self):
        """Each schedule's total fraction should be < 1 (remaining rides trailing)."""
        from strategies.smc_trend import _PARTIAL_SCHEDULES
        for regime, schedule in _PARTIAL_SCHEDULES.items():
            total = sum(f for _, f in schedule)
            assert total < 1.0, f"{regime} total fraction {total} >= 1.0"


class TestGuardLayers:
    """Test layered guard pipeline."""

    def _make_pipeline(self):
        import guards.pipeline as gp
        from guards.pipeline import create_default_pipeline
        gp._default_pipeline = None
        return create_default_pipeline()

    def test_pipeline_has_three_layers(self):
        pipeline = self._make_pipeline()
        assert len(pipeline.layers) == 3

    def test_layer_names(self):
        pipeline = self._make_pipeline()
        names = [l.name for l in pipeline.layers]
        assert names == ["account", "strategy", "trade"]

    def test_layer_alert_levels(self):
        pipeline = self._make_pipeline()
        levels = [l.alert_level for l in pipeline.layers]
        assert levels == ["critical", "warning", "info"]

    def test_flat_guards_list_backward_compatible(self):
        """pipeline.guards should return flat list of all guards from all layers."""
        pipeline = self._make_pipeline()
        assert len(pipeline.guards) == 9  # 3 + 3 + 3

    def test_account_layer_has_drawdown_first(self):
        from guards.guards import DrawdownGuard
        pipeline = self._make_pipeline()
        account_layer = pipeline.layers[0]
        assert isinstance(account_layer.guards[0], DrawdownGuard)

    def test_rejection_includes_layer_prefix(self):
        from guards.base import GuardContext, GuardLayer, GuardPipeline, Guard

        class AlwaysReject(Guard):
            def check(self, ctx):
                return "test rejection"

        pipeline = GuardPipeline(layers=[
            GuardLayer(name="account", guards=[AlwaysReject()], alert_level="critical"),
        ])
        ctx = GuardContext(symbol="BTC", side="long", amount=100, leverage=1, account_balance=1000)
        result = pipeline.run(ctx)
        assert result is not None
        assert "[L:account]" in result
        assert "test rejection" in result

    def test_all_pass_returns_none(self):
        from guards.base import GuardContext, GuardLayer, GuardPipeline, Guard

        class AlwaysPass(Guard):
            def check(self, ctx):
                return None

        pipeline = GuardPipeline(layers=[
            GuardLayer(name="account", guards=[AlwaysPass()], alert_level="critical"),
            GuardLayer(name="trade", guards=[AlwaysPass()], alert_level="info"),
        ])
        ctx = GuardContext(symbol="BTC", side="long", amount=100, leverage=1, account_balance=1000)
        assert pipeline.run(ctx) is None

    def test_layer1_rejection_skips_layer2(self):
        """Account-level rejection should prevent strategy/trade guards from running."""
        from guards.base import GuardContext, GuardLayer, GuardPipeline, Guard

        call_log = []

        class RejectGuard(Guard):
            def check(self, ctx):
                call_log.append("reject")
                return "account blocked"

        class TrackGuard(Guard):
            def check(self, ctx):
                call_log.append("track")
                return None

        pipeline = GuardPipeline(layers=[
            GuardLayer(name="account", guards=[RejectGuard()], alert_level="critical"),
            GuardLayer(name="strategy", guards=[TrackGuard()], alert_level="warning"),
        ])
        ctx = GuardContext(symbol="BTC", side="long", amount=100, leverage=1, account_balance=1000)
        pipeline.run(ctx)
        assert call_log == ["reject"]  # strategy guard never called
