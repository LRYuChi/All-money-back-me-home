"""Tests for CategorySpecializationFeature."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.features.category_specialization import (
    UNKNOWN_CATEGORY,
    CategorySpecializationFeature,
)

NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def _pos(condition_id: str, won: bool, days_ago: int = 5) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId=condition_id,
        outcome="Yes",
        size=Decimal("100"),
        cashPnl=Decimal("100") if won else Decimal("-50"),
        curPrice=Decimal("1") if won else Decimal("0"),
        initialValue=Decimal("50"),
        redeemable=True,
        endDate=NOW - timedelta(days=days_ago),
    )


def _ctx(positions: list[Position], market_categories: dict[str, str]) -> ScanContext:
    return ScanContext(
        wallet_address="0xw",
        trades=[],
        positions=positions,
        now=NOW,
        pre_reg=load_pre_registered(),
        market_categories=market_categories,
    )


@pytest.fixture
def feature():
    return CategorySpecializationFeature()


class TestCategorySpecialization:
    def test_low_samples_when_below_min_total(self, feature):
        # 5 positions; threshold is 10
        positions = [_pos(f"0x{i}", won=True) for i in range(5)]
        cats = {f"0x{i}": "Politics" for i in range(5)}
        result = feature.compute(_ctx(positions, cats))
        assert result.confidence == "low_samples"

    def test_identifies_specialist_category(self, feature):
        # Wallet with 20 resolved: 12 in Politics (10 wins, 83%), 8 in Sports (4 wins, 50%)
        # Overall 14/20 = 70%, Politics lift = 13pct > 10pct → specialist
        positions = []
        cats = {}
        for i in range(12):
            cid = f"0xpol{i}"
            positions.append(_pos(cid, won=(i < 10)))  # 10/12 wins
            cats[cid] = "Politics"
        for i in range(8):
            cid = f"0xsp{i}"
            positions.append(_pos(cid, won=(i < 4)))  # 4/8 wins
            cats[cid] = "Sports"

        result = feature.compute(_ctx(positions, cats))
        assert result.confidence == "ok"
        assert "Politics" in result.value["specialist_categories"]
        assert "Sports" not in result.value["specialist_categories"]
        assert result.value["primary_category"] == "Politics"
        assert result.value["category_count"] == 2

    def test_no_specialist_when_uniform_winrate(self, feature):
        # 20 positions across 2 cats, both ~50% win rate
        positions = []
        cats = {}
        for i in range(10):
            cid = f"0xpol{i}"
            positions.append(_pos(cid, won=(i < 5)))
            cats[cid] = "Politics"
        for i in range(10):
            cid = f"0xsp{i}"
            positions.append(_pos(cid, won=(i < 5)))
            cats[cid] = "Sports"
        result = feature.compute(_ctx(positions, cats))
        assert result.value["specialist_categories"] == []

    def test_low_samples_when_unknown_ratio_high(self, feature):
        # 12 positions, 7 with unknown category (>50% threshold)
        positions = [_pos(f"0x{i}", won=True) for i in range(12)]
        cats = {f"0x{i}": "Politics" for i in range(5)}  # only 5 known, 7 unknown
        result = feature.compute(_ctx(positions, cats))
        assert result.confidence == "low_samples"
        assert result.value["unknown_ratio"] > 0.5

    def test_unknown_category_excluded_from_specialist(self, feature):
        # All 15 positions in unknown category, all won
        positions = [_pos(f"0x{i}", won=True) for i in range(15)]
        cats = {}  # zero coverage
        result = feature.compute(_ctx(positions, cats))
        # Should be low_samples due to high unknown ratio
        assert result.confidence == "low_samples"
        # Even if we had ok confidence, unknown shouldn't be specialist
        assert UNKNOWN_CATEGORY not in result.value.get("specialist_categories", [])

    def test_categories_dict_has_metrics(self, feature):
        positions = [_pos(f"0xpol{i}", won=(i < 8)) for i in range(10)] + [
            _pos(f"0xsp{i}", won=True) for i in range(5)
        ]
        cats = {f"0xpol{i}": "Politics" for i in range(10)}
        cats.update({f"0xsp{i}": "Sports" for i in range(5)})
        result = feature.compute(_ctx(positions, cats))
        assert result.value["categories"]["Politics"]["resolved"] == 10
        assert result.value["categories"]["Politics"]["wins"] == 8
        assert result.value["categories"]["Politics"]["win_rate"] == 0.8
        assert result.value["categories"]["Sports"]["resolved"] == 5
        assert result.value["categories"]["Sports"]["sufficient_samples"] is True
