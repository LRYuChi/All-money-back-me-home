"""Tests for PositionConfidenceFeature (1.5c.3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.features.position_confidence import PositionConfidenceFeature

NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)


def _pos(
    *,
    won: bool,
    initial_value: float,
    days_ago: int = 10,
    condition_id: str = "0xA",
) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId=condition_id,
        outcome="Yes",
        size=Decimal(str(initial_value * 2)),
        avgPrice=Decimal("0.5"),
        initialValue=Decimal(str(initial_value)),
        currentValue=Decimal(str(initial_value * 2 if won else 0)),
        cashPnl=Decimal(str(initial_value if won else -initial_value)),
        curPrice=Decimal("1" if won else "0"),
        redeemable=True,
        endDate=NOW - timedelta(days=days_ago),
    )


def _ctx(positions: list[Position]) -> ScanContext:
    return ScanContext(
        wallet_address="0xw",
        trades=[],
        positions=positions,
        now=NOW,
        pre_reg=load_pre_registered(),
        market_categories={},
    )


class TestPositionConfidenceFeature:
    def test_insufficient_resolved_returns_low_samples(self):
        positions = [_pos(won=True, initial_value=500) for _ in range(5)]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.confidence == "low_samples"
        assert result.value["is_confidence_sized"] is False

    def test_insufficient_winners_returns_low_samples(self):
        # 30 resolved, but only 5 winners
        positions = [
            _pos(won=True, initial_value=500, condition_id=f"0xW{i}") for i in range(5)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(25)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.confidence == "low_samples"
        assert result.value["n_winners"] == 5

    def test_confidence_sized_fires_when_winners_bigger(self):
        # 15 winners @ $1000, 15 losers @ $500 → ratio = 2.0
        positions = [
            _pos(won=True, initial_value=1000, condition_id=f"0xW{i}") for i in range(15)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(15)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.confidence == "ok"
        assert result.value["is_confidence_sized"] is True
        assert result.value["is_reverse_sized"] is False
        assert result.value["size_ratio_winners_over_losers"] == pytest.approx(2.0)
        assert result.value["winner_avg_notional"] == pytest.approx(1000)
        assert result.value["loser_avg_notional"] == pytest.approx(500)

    def test_reverse_sized_fires_when_losers_bigger(self):
        # 15 winners @ $500, 15 losers @ $1000 → ratio = 0.5 < 1/1.2 = 0.833
        positions = [
            _pos(won=True, initial_value=500, condition_id=f"0xW{i}") for i in range(15)
        ] + [
            _pos(won=False, initial_value=1000, condition_id=f"0xL{i}") for i in range(15)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.value["is_confidence_sized"] is False
        assert result.value["is_reverse_sized"] is True

    def test_flat_sizing_neither_flag(self):
        # 同樣大小 → ratio = 1.0，落在兩門檻中間
        positions = [
            _pos(won=True, initial_value=500, condition_id=f"0xW{i}") for i in range(15)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(15)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.value["is_confidence_sized"] is False
        assert result.value["is_reverse_sized"] is False

    def test_notional_cv_computed(self):
        positions = [
            _pos(won=True, initial_value=1000 * (1 + i % 3), condition_id=f"0xW{i}")
            for i in range(15)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(15)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert result.value["notional_cv"] > 0
        assert result.value["avg_notional_overall"] > 0

    def test_result_structure(self):
        positions = [
            _pos(won=True, initial_value=1000, condition_id=f"0xW{i}") for i in range(15)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(15)
        ]
        result = PositionConfidenceFeature().compute(_ctx(positions))
        assert set(result.value.keys()) >= {
            "is_confidence_sized",
            "is_reverse_sized",
            "size_ratio_winners_over_losers",
            "winner_avg_notional",
            "loser_avg_notional",
            "n_winners",
            "n_losers",
            "n_settled",
            "avg_notional_overall",
            "notional_std",
            "notional_cv",
        }


class TestRegistryAndIntegration:
    def test_in_registry(self):
        from polymarket.scanner.features import REGISTRY

        assert "position_confidence" in REGISTRY
        assert REGISTRY["position_confidence"].version == "1.0"

    def test_enabled_in_current_version(self):
        from polymarket.scanner import SCANNER_VERSION

        assert SCANNER_VERSION == "1.5c.3"
        pre_reg = load_pre_registered()
        enabled = pre_reg["scanner"]["features"]["enabled_in_version"][SCANNER_VERSION]
        assert "position_confidence" in enabled

    def test_scan_wallet_integrates(self):
        from polymarket.scanner.scan import scan_wallet

        positions = [
            _pos(won=True, initial_value=1000, condition_id=f"0xW{i}") for i in range(15)
        ] + [
            _pos(won=False, initial_value=500, condition_id=f"0xL{i}") for i in range(15)
        ]
        profile = scan_wallet("0xw", [], positions, now=NOW)
        assert "position_confidence" in profile.features
        pc = profile.features["position_confidence"]
        assert pc.confidence == "ok"
        assert pc.value["is_confidence_sized"] is True
