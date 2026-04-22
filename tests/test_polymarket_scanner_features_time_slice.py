"""Tests for TimeSliceConsistencyFeature."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.features.time_slice_consistency import (
    TimeSliceConsistencyFeature,
)

NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def _pos(days_ago: int, won: bool) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId=f"0x{days_ago}",
        outcome="Yes",
        size=Decimal("100"),
        cashPnl=Decimal("100") if won else Decimal("-50"),
        curPrice=Decimal("1") if won else Decimal("0"),
        initialValue=Decimal("50"),
        redeemable=True,
        endDate=NOW - timedelta(days=days_ago),
    )


def _ctx(positions):
    return ScanContext(
        wallet_address="0xw",
        trades=[],
        positions=positions,
        now=NOW,
        pre_reg=load_pre_registered(),
        market_categories={},
    )


@pytest.fixture
def feature():
    return TimeSliceConsistencyFeature()


class TestTimeSliceConsistency:
    def test_low_samples_when_no_resolved(self, feature):
        result = feature.compute(_ctx([]))
        assert result.confidence == "low_samples"
        assert result.value["valid_segments"] == 0

    def test_low_samples_when_only_one_segment_has_data(self, feature):
        # All in last 30 days
        positions = [_pos(days_ago=10, won=True) for _ in range(10)]
        result = feature.compute(_ctx(positions))
        assert result.confidence == "low_samples"
        assert result.value["valid_segments"] == 1
        assert result.value["consistent"] is False

    def test_consistent_when_uniform_win_rates(self, feature):
        positions = []
        # 3 segments × 5 positions × 60% win rate (3 wins, 2 losses each segment)
        for seg in range(3):
            base_day = seg * 30 + 5
            for i in range(5):
                positions.append(_pos(days_ago=base_day + i * 2, won=(i < 3)))
        result = feature.compute(_ctx(positions))
        assert result.confidence == "ok"
        assert result.value["valid_segments"] == 3
        assert abs(result.value["win_rate_mean"] - 0.6) < 0.01
        assert result.value["win_rate_std"] == 0.0
        assert result.value["consistent"] is True

    def test_inconsistent_when_high_variance(self, feature):
        positions = []
        # Segment 0 (recent): 5 positions, all win (100%)
        for i in range(5):
            positions.append(_pos(days_ago=5 + i, won=True))
        # Segment 1: 5 positions, all lose (0%)
        for i in range(5):
            positions.append(_pos(days_ago=35 + i, won=False))
        # Segment 2: 5 positions, mixed (60%)
        for i in range(5):
            positions.append(_pos(days_ago=65 + i, won=(i < 3)))
        result = feature.compute(_ctx(positions))
        assert result.value["valid_segments"] == 3
        assert result.value["win_rate_std"] > 0.15  # high variance
        assert result.value["consistent"] is False

    def test_segments_in_correct_time_order(self, feature):
        positions = []
        for seg in range(3):
            for i in range(5):
                positions.append(_pos(days_ago=seg * 30 + 5 + i, won=True))
        result = feature.compute(_ctx(positions))
        # segment 0 is most recent
        assert result.value["segments"][0]["days_back"] == [0, 30]
        assert result.value["segments"][1]["days_back"] == [30, 60]
        assert result.value["segments"][2]["days_back"] == [60, 90]

    def test_insufficient_segment_marked(self, feature):
        # Only 2 positions in segment 0 (< 3 needed)
        positions = [
            _pos(days_ago=5, won=True),
            _pos(days_ago=10, won=True),
            # full segment 1 and 2
            *[_pos(days_ago=35 + i, won=(i < 3)) for i in range(5)],
            *[_pos(days_ago=65 + i, won=(i < 3)) for i in range(5)],
        ]
        result = feature.compute(_ctx(positions))
        assert result.value["segments"][0]["sufficient"] is False
        assert result.value["segments"][0]["win_rate"] is None
        assert result.value["segments"][1]["sufficient"] is True
        assert result.value["segments"][2]["sufficient"] is True
        # 2 valid out of 3 → ok confidence (consistent may still be False due to <num_segments)
        assert result.value["valid_segments"] == 2
