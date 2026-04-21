"""Tests for polymarket.config — pre_registered.yaml 讀取與 get_threshold 路徑查詢."""

from __future__ import annotations

import pytest

from polymarket.config import get_threshold, load_pre_registered


class TestPreRegisteredLoading:
    def test_loads_yaml(self):
        cfg = load_pre_registered()
        assert "whale_tiers" in cfg
        assert "strategy_promotion" in cfg
        assert "capital_ladder" in cfg
        assert "circuit_breakers" in cfg

    def test_whale_tier_a_thresholds(self):
        assert get_threshold("whale_tiers.A.min_win_rate.value") == 0.60
        assert get_threshold("whale_tiers.A.min_trades_90d.value") == 20

    def test_whale_tier_c_thresholds(self):
        assert get_threshold("whale_tiers.C.min_win_rate.value") == 0.50

    def test_brier_threshold(self):
        assert get_threshold("strategy_promotion.brier_score.max_threshold.value") == 0.22

    def test_capital_ladder_month_1(self):
        assert get_threshold("capital_ladder.month_1.capital_usdc.value") == 50

    def test_circuit_breaker_per_position(self):
        assert get_threshold("circuit_breakers.per_position_max_ratio.value") == 0.02

    def test_raises_on_invalid_path(self):
        with pytest.raises(KeyError):
            get_threshold("nonexistent.path.value")

    def test_every_threshold_has_rationale_and_review(self):
        """確保每個 value 欄位都搭配 set_at / rationale / next_review（憲法強制）."""
        cfg = load_pre_registered()

        def walk(node, path=""):
            if isinstance(node, dict):
                if "value" in node and not isinstance(node["value"], dict):
                    # leaf threshold
                    missing = [k for k in ("set_at", "rationale", "next_review") if k not in node]
                    assert not missing, f"{path} 缺少欄位: {missing}"
                    return
                for k, v in node.items():
                    walk(v, f"{path}.{k}" if path else k)

        # meta 區段不需要 rationale，排除
        for key, sub in cfg.items():
            if key == "meta":
                continue
            walk(sub, key)
