"""Tests for alpha_hunter archetype (1.5c.1 addition)."""

from __future__ import annotations

from polymarket.config import load_pre_registered
from polymarket.scanner.classify import (
    ARCHETYPE_ALPHA_HUNTER,
    ARCHETYPE_STEADY_GROWER,
    classify_archetypes,
)
from polymarket.scanner.profile import FeatureResult


def _brier(value: dict, *, confidence: str = "ok") -> FeatureResult:
    return FeatureResult(
        feature_name="brier_calibration",
        feature_version="1.0",
        value=value,
        confidence=confidence,
        sample_size=40,
    )


class TestAlphaHunterArchetype:
    def test_fires_when_edge_and_samples_ok(self):
        features = {
            "brier_calibration": _brier({"market_edge": 0.15, "n_analyzed": 40}),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_ALPHA_HUNTER in tags

    def test_below_edge_threshold_no_fire(self):
        features = {
            "brier_calibration": _brier({"market_edge": 0.05, "n_analyzed": 40}),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_ALPHA_HUNTER not in tags

    def test_below_sample_threshold_no_fire(self):
        features = {
            "brier_calibration": _brier({"market_edge": 0.15, "n_analyzed": 10}),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_ALPHA_HUNTER not in tags

    def test_low_samples_confidence_no_fire(self):
        features = {
            "brier_calibration": _brier(
                {"market_edge": 0.20, "n_analyzed": 50}, confidence="low_samples"
            ),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_ALPHA_HUNTER not in tags

    def test_excluded_tier_no_fire(self):
        features = {
            "brier_calibration": _brier({"market_edge": 0.15, "n_analyzed": 40}),
        }
        tags = classify_archetypes(features, "excluded", pre_reg=load_pre_registered())
        assert tags == []

    def test_missing_fields_no_fire(self):
        features = {
            "brier_calibration": _brier({"market_edge": None, "n_analyzed": 40}),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_ALPHA_HUNTER not in tags

    def test_coexists_with_other_archetypes(self):
        from polymarket.scanner.profile import FeatureResult

        features = {
            "steady_growth": FeatureResult(
                feature_name="steady_growth",
                feature_version="1.1",
                value={"is_steady_grower": True},
                confidence="ok",
                sample_size=40,
            ),
            "brier_calibration": _brier({"market_edge": 0.15, "n_analyzed": 40}),
        }
        tags = classify_archetypes(features, "A", pre_reg=load_pre_registered())
        assert ARCHETYPE_STEADY_GROWER in tags
        assert ARCHETYPE_ALPHA_HUNTER in tags
        # 順序：steady_grower 先於 alpha_hunter (per _ARCHETYPE_ORDER)
        assert tags.index(ARCHETYPE_STEADY_GROWER) < tags.index(ARCHETYPE_ALPHA_HUNTER)

    def test_fallback_thresholds_when_yaml_missing(self):
        """若 pre_reg 缺 archetype_alpha_hunter 區塊，使用保守預設."""
        features = {
            "brier_calibration": _brier({"market_edge": 0.10, "n_analyzed": 30}),
        }
        # Empty pre_reg forces fallback to hardcoded defaults (0.08 / 30)
        tags = classify_archetypes(features, "A", pre_reg={})
        assert ARCHETYPE_ALPHA_HUNTER in tags

    def test_fallback_thresholds_reject_below(self):
        features = {
            "brier_calibration": _brier({"market_edge": 0.05, "n_analyzed": 30}),
        }
        tags = classify_archetypes(features, "A", pre_reg={})
        assert ARCHETYPE_ALPHA_HUNTER not in tags


class TestEndToEnd:
    def test_scan_wallet_produces_alpha_hunter(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from polymarket.models import Position, Trade
        from polymarket.scanner.scan import scan_wallet

        NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        # 40 positions bought at 0.3, all won → market_edge ≈ 0.7 (高 alpha)
        positions = [
            Position(
                proxyWallet="0xw",
                conditionId=f"0x{i:02d}",
                outcome="Yes",
                size=Decimal("1000"),
                avgPrice=Decimal("0.3"),
                initialValue=Decimal("300"),
                currentValue=Decimal("1000"),
                cashPnl=Decimal("700"),
                curPrice=Decimal("1"),
                redeemable=True,
                endDate=NOW - timedelta(days=80 - i * 2),
            )
            for i in range(40)
        ]
        # Trades needed to pass coarse_filter (tier != excluded)
        trades = [
            Trade(
                id=f"t-{i}",
                market=f"0x{i:02d}",
                asset_id="tok1",
                price=Decimal("0.3"),
                size=Decimal("1000"),
                side="BUY",
                match_time=NOW - timedelta(days=70 - i * 2),
            )
            for i in range(30)
        ]
        profile = scan_wallet("0xw", trades, positions, now=NOW)
        assert ARCHETYPE_ALPHA_HUNTER in profile.archetypes
