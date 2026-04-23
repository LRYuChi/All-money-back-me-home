"""Tests for classify_archetypes — 1.5c archetype classifier."""

from __future__ import annotations

from polymarket.scanner.classify import (
    ARCHETYPE_CONSISTENT_TRADER,
    ARCHETYPE_DOMAIN_SPECIALIST,
    ARCHETYPE_STEADY_GROWER,
    classify_archetypes,
)
from polymarket.scanner.profile import FeatureResult


def _feat(name: str, value: dict, *, confidence: str = "ok") -> FeatureResult:
    return FeatureResult(
        feature_name=name,
        feature_version="1.0",
        value=value,
        confidence=confidence,
        sample_size=50,
    )


class TestArchetypeClassifier:
    def test_excluded_tier_returns_empty(self):
        features = {
            "steady_growth": _feat("steady_growth", {"is_steady_grower": True}),
        }
        assert classify_archetypes(features, "excluded", pre_reg={}) == []

    def test_no_features_returns_empty(self):
        assert classify_archetypes({}, "A", pre_reg={}) == []

    def test_steady_grower_tag(self):
        features = {"steady_growth": _feat("steady_growth", {"is_steady_grower": True})}
        tags = classify_archetypes(features, "A", pre_reg={})
        assert tags == [ARCHETYPE_STEADY_GROWER]

    def test_steady_grower_requires_confidence_ok(self):
        features = {
            "steady_growth": _feat(
                "steady_growth", {"is_steady_grower": True}, confidence="low_samples"
            )
        }
        assert classify_archetypes(features, "A", pre_reg={}) == []

    def test_domain_specialist_tag(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {"specialist_categories": ["Politics"]},
            )
        }
        tags = classify_archetypes(features, "A", pre_reg={})
        assert tags == [ARCHETYPE_DOMAIN_SPECIALIST]

    def test_domain_specialist_requires_non_empty_list(self):
        features = {
            "category_specialization": _feat(
                "category_specialization",
                {"specialist_categories": []},
            )
        }
        assert classify_archetypes(features, "A", pre_reg={}) == []

    def test_consistent_trader_tag(self):
        features = {
            "time_slice_consistency": _feat(
                "time_slice_consistency", {"consistent": True}
            )
        }
        tags = classify_archetypes(features, "A", pre_reg={})
        assert tags == [ARCHETYPE_CONSISTENT_TRADER]

    def test_consistent_trader_false_does_not_fire(self):
        features = {
            "time_slice_consistency": _feat(
                "time_slice_consistency", {"consistent": False}
            )
        }
        assert classify_archetypes(features, "A", pre_reg={}) == []

    def test_multi_label_all_three(self):
        features = {
            "steady_growth": _feat("steady_growth", {"is_steady_grower": True}),
            "category_specialization": _feat(
                "category_specialization", {"specialist_categories": ["Politics", "Sports"]}
            ),
            "time_slice_consistency": _feat(
                "time_slice_consistency", {"consistent": True}
            ),
        }
        tags = classify_archetypes(features, "A", pre_reg={})
        # 順序必須穩定
        assert tags == [
            ARCHETYPE_STEADY_GROWER,
            ARCHETYPE_DOMAIN_SPECIALIST,
            ARCHETYPE_CONSISTENT_TRADER,
        ]

    def test_partial_multi_label(self):
        # 只有 steady_grower + consistent_trader（跳過中間的 domain_specialist）
        features = {
            "steady_growth": _feat("steady_growth", {"is_steady_grower": True}),
            "time_slice_consistency": _feat(
                "time_slice_consistency", {"consistent": True}
            ),
        }
        tags = classify_archetypes(features, "A", pre_reg={})
        assert tags == [ARCHETYPE_STEADY_GROWER, ARCHETYPE_CONSISTENT_TRADER]

    def test_none_value_does_not_fire(self):
        features = {
            "steady_growth": FeatureResult(
                feature_name="steady_growth",
                feature_version="1.1",
                value=None,
                confidence="ok",
                sample_size=50,
            )
        }
        assert classify_archetypes(features, "A", pre_reg={}) == []

    def test_consistent_none_treated_as_not_fired(self):
        # consistent=None 表示 low_samples 情境，不應觸發
        features = {
            "time_slice_consistency": _feat(
                "time_slice_consistency", {"consistent": None}
            )
        }
        assert classify_archetypes(features, "A", pre_reg={}) == []


class TestIntegration:
    """端對端：scan_wallet 應該在合適資料下輸出 archetype."""

    def test_scan_wallet_produces_archetypes(self):
        from datetime import datetime, timedelta, timezone
        from decimal import Decimal

        from polymarket.models import Position, Trade
        from polymarket.scanner.scan import scan_wallet

        NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)

        # 造出能同時觸發 steady_grower 與 consistent_trader 的錢包
        positions = [
            Position(
                proxyWallet="0xw",
                conditionId=f"0x{i:02d}",
                outcome="Yes",
                size=Decimal("100"),
                avgPrice=Decimal("0.5"),
                initialValue=Decimal("500"),
                cashPnl=Decimal("200"),
                curPrice=Decimal("1"),
                redeemable=True,
                endDate=NOW - timedelta(days=80 - i * 2),
            )
            for i in range(40)
        ]
        trades = [
            Trade(
                id=f"t-{i}",
                market=f"0x{i:02d}",
                asset_id="tok1",
                price=Decimal("0.5"),
                size=Decimal("1000"),
                side="BUY",
                match_time=NOW - timedelta(days=70 - i * 2),
            )
            for i in range(30)
        ]
        profile = scan_wallet("0xw", trades, positions, now=NOW)
        # 全勝 + smooth curve → steady_grower
        assert ARCHETYPE_STEADY_GROWER in profile.archetypes
        # 全勝跨段 → consistent_trader（time_slice std = 0）
        assert ARCHETYPE_CONSISTENT_TRADER in profile.archetypes
