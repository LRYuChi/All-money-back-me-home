"""Tests for BrierCalibrationFeature — 機率校準 / market_edge."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.features.brier_calibration import BrierCalibrationFeature

NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)


def _pos(
    *,
    days_ago: int,
    pnl: float,
    won: bool,
    avg_price: float = 0.5,
    initial_value: float = 500.0,
    condition_id: str = "0xA",
) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId=condition_id,
        outcome="Yes",
        size=Decimal(str(initial_value / max(avg_price, 0.01))),
        avgPrice=Decimal(str(avg_price)),
        initialValue=Decimal(str(initial_value)),
        currentValue=Decimal(str(initial_value * 2 if won else 0)),
        cashPnl=Decimal(str(pnl)),
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


class TestBrierFeature:
    def test_insufficient_samples_returns_low_samples(self):
        positions = [_pos(days_ago=10, pnl=100, won=True) for _ in range(5)]
        result = BrierCalibrationFeature().compute(_ctx(positions))
        assert result.confidence == "low_samples"
        assert result.value["brier_score"] is None
        assert result.value["n_settled"] == 5

    def test_brier_math_correct(self):
        # 40 positions all bought at 0.3, all won
        # Brier = (1/N) * (0.3 - 1)² = 0.49 for each → mean = 0.49
        positions = [
            _pos(days_ago=80 - i * 2, pnl=200, won=True, avg_price=0.3, condition_id=f"0x{i:02d}")
            for i in range(40)
        ]
        result = BrierCalibrationFeature().compute(_ctx(positions))
        assert result.confidence == "ok"
        assert result.value["brier_score"] == pytest.approx(0.49, abs=0.01)

    def test_market_edge_positive_when_whale_wins_against_consensus(self):
        # 40 positions at p=0.3, all won → actual_wr = 1.0, avg_entry = 0.3, edge = 0.7
        positions = [
            _pos(days_ago=80 - i * 2, pnl=200, won=True, avg_price=0.3, condition_id=f"0x{i:02d}")
            for i in range(40)
        ]
        result = BrierCalibrationFeature().compute(_ctx(positions))
        assert result.value["market_edge"] == pytest.approx(0.70, abs=0.01)
        assert result.value["actual_win_rate"] == pytest.approx(1.0)
        assert result.value["avg_entry_price"] == pytest.approx(0.3, abs=0.01)

    def test_market_edge_zero_when_wallet_matches_market(self):
        # Half win at 0.5, half lose at 0.5 → edge = 0
        positions = []
        for i in range(20):
            positions.append(_pos(days_ago=80 - i * 2, pnl=100, won=True, avg_price=0.5, condition_id=f"0x{i:02d}"))
        for i in range(20):
            positions.append(_pos(days_ago=40 - i * 2, pnl=-100, won=False, avg_price=0.5, condition_id=f"0y{i:02d}"))
        result = BrierCalibrationFeature().compute(_ctx(positions))
        assert abs(result.value["market_edge"]) < 0.05

    def test_calibration_buckets_populated(self):
        # Spread prices across buckets
        positions = []
        for i in range(40):
            price = 0.2 + (i % 5) * 0.15  # bounces across 0.2-0.8
            positions.append(
                _pos(
                    days_ago=80 - i * 2,
                    pnl=100,
                    won=(i % 2 == 0),
                    avg_price=price,
                    condition_id=f"0x{i:02d}",
                )
            )
        result = BrierCalibrationFeature().compute(_ctx(positions))
        buckets = result.value["calibration"]["buckets"]
        assert len(buckets) == 5
        # 至少有幾個 bucket 樣本充分
        sufficient_count = sum(1 for b in buckets if b["sufficient"])
        assert sufficient_count >= 2

    def test_extreme_prices_excluded(self):
        # All at p=0 (extreme) — should be excluded → low_samples / all filtered
        positions = [
            _pos(days_ago=80 - i * 2, pnl=100, won=True, avg_price=0.0001, condition_id=f"0x{i:02d}")
            for i in range(40)
        ]
        # avg_price=0 will be filtered (< 0 or >=1)
        # Actually 0.0001 is ok (between 0 and 1). Use 0.0 to force extreme filtering.
        for p in positions:
            p.avg_price = Decimal("0")
        result = BrierCalibrationFeature().compute(_ctx(positions))
        # All filtered → low_samples
        assert result.confidence == "low_samples"

    def test_result_has_all_keys(self):
        positions = [
            _pos(days_ago=80 - i * 2, pnl=100, won=(i % 2 == 0), avg_price=0.5, condition_id=f"0x{i:02d}")
            for i in range(40)
        ]
        result = BrierCalibrationFeature().compute(_ctx(positions))
        v = result.value
        assert set(v.keys()) >= {
            "brier_score",
            "avg_entry_price",
            "actual_win_rate",
            "market_edge",
            "calibration",
            "n_settled",
            "reference_strategy_brier_threshold",
        }


class TestRegistryAndIntegration:
    def test_in_registry(self):
        from polymarket.scanner.features import REGISTRY

        assert "brier_calibration" in REGISTRY
        assert REGISTRY["brier_calibration"].version == "1.0"

    def test_enabled_in_current_version(self):
        from polymarket.scanner import SCANNER_VERSION

        pre_reg = load_pre_registered()
        enabled = pre_reg["scanner"]["features"]["enabled_in_version"][SCANNER_VERSION]
        assert "brier_calibration" in enabled

    def test_scan_wallet_integrates(self):
        from polymarket.scanner.scan import scan_wallet

        positions = [
            _pos(days_ago=80 - i * 2, pnl=200, won=True, avg_price=0.3, condition_id=f"0x{i:02d}")
            for i in range(40)
        ]
        profile = scan_wallet("0xw", [], positions, now=NOW)
        assert "brier_calibration" in profile.features
        brier = profile.features["brier_calibration"]
        assert brier.confidence == "ok"
        assert brier.value["market_edge"] > 0.5
