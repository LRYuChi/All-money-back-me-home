"""Tests for SteadyGrowthFeature — equity curve smoothness."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position, Trade
from polymarket.scanner.features.base import ScanContext
from polymarket.scanner.features.steady_growth import (
    SteadyGrowthFeature,
    _build_realized_pnl_curve,
    _compute_gain_to_pain_ratio,
    _compute_longest_losing_streak,
    _compute_max_drawdown,
    _compute_new_high_frequency,
    _compute_r_squared,
    _compute_segment_pnls,
)

NOW = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _pos(
    *,
    days_ago: int,
    pnl: float,
    won: bool,
    condition_id: str = "0xA",
    initial_value: float = 500.0,
    avg_price: float = 0.5,
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


def _trade(days_ago: int, tid: str = "t") -> Trade:
    return Trade(
        id=f"{tid}:{days_ago}",
        market="0xA",
        asset_id="tok1",
        price=Decimal("0.5"),
        size=Decimal("1000"),
        side="BUY",
        match_time=NOW - timedelta(days=days_ago),
    )


def _ctx(
    positions: list[Position],
    *,
    pre_reg: dict | None = None,
    trades: list[Trade] | None = None,
) -> ScanContext:
    return ScanContext(
        wallet_address="0xw",
        trades=trades or [],
        positions=positions,
        now=NOW,
        pre_reg=pre_reg or load_pre_registered(),
        market_categories={},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Pure math helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestMaxDrawdown:
    def test_empty(self):
        assert _compute_max_drawdown([]) == (0.0, 0.0)

    def test_monotonic_up(self):
        assert _compute_max_drawdown([0, 100, 200, 300]) == (0.0, 0.0)

    def test_with_dip(self):
        amt, ratio = _compute_max_drawdown([0, 100, 200, 150, 180])
        assert amt == pytest.approx(50.0)
        assert ratio == pytest.approx(0.25)

    def test_all_negative(self):
        _, ratio = _compute_max_drawdown([-100, -200, -300])
        assert ratio == 1.0


class TestRSquared:
    def test_perfect_line(self):
        assert _compute_r_squared([0, 1, 2, 3, 4, 5]) == pytest.approx(1.0)

    def test_too_few_samples(self):
        assert _compute_r_squared([1.0]) == 0.0

    def test_noisy_trend_high_r2(self):
        r2 = _compute_r_squared([0.0, 1.2, 1.9, 3.1, 4.0, 5.2, 6.0])
        assert 0.9 <= r2 <= 1.0


class TestGainToPainRatio:
    def test_monotonic_returns_cap(self):
        assert _compute_gain_to_pain_ratio([0, 100, 200, 300]) == 3.0

    def test_with_drawdown(self):
        # gain = 200, max_dd = 50, ratio = 4.0
        assert _compute_gain_to_pain_ratio([0, 100, 200, 150, 200]) == pytest.approx(4.0)

    def test_flat_zero(self):
        assert _compute_gain_to_pain_ratio([100, 100]) == 0.0


class TestNewHighFrequency:
    def test_monotonic_up(self):
        assert _compute_new_high_frequency([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) == 1.0

    def test_flat(self):
        assert _compute_new_high_frequency([5, 5, 5, 5]) == 0.0

    def test_window_truncation(self):
        curve = list(range(20)) + [19] * 30
        assert _compute_new_high_frequency(curve, days=30) == 0.0


class TestLongestLosingStreak:
    def test_mixed(self):
        positions = [
            _pos(days_ago=60, pnl=-50, won=False),
            _pos(days_ago=50, pnl=-50, won=False),
            _pos(days_ago=40, pnl=100, won=True),
            _pos(days_ago=30, pnl=-50, won=False),
            _pos(days_ago=20, pnl=-50, won=False),
            _pos(days_ago=10, pnl=-50, won=False),
            _pos(days_ago=5, pnl=100, won=True),
        ]
        assert _compute_longest_losing_streak(positions) == 3

    def test_no_losses(self):
        assert _compute_longest_losing_streak(
            [_pos(days_ago=i, pnl=100, won=True) for i in range(10, 5, -1)]
        ) == 0


class TestSegmentPnls:
    def test_three_segments(self):
        positions = [
            _pos(days_ago=10, pnl=100, won=True),
            _pos(days_ago=40, pnl=200, won=True),
            _pos(days_ago=70, pnl=-50, won=False),
        ]
        assert _compute_segment_pnls(positions, now=NOW) == [100.0, 200.0, -50.0]


class TestBuildRealizedPnlCurve:
    def test_empty(self):
        dates, values = _build_realized_pnl_curve([], now=NOW)
        assert dates == [] and values == []

    def test_single_resolution(self):
        positions = [_pos(days_ago=5, pnl=100, won=True)]
        dates, values = _build_realized_pnl_curve(positions, now=NOW)
        assert len(dates) == 6
        assert values[0] == 100.0
        assert values[-1] == 100.0

    def test_cumulative(self):
        positions = [
            _pos(days_ago=10, pnl=100, won=True),
            _pos(days_ago=5, pnl=200, won=True),
            _pos(days_ago=3, pnl=-50, won=False),
        ]
        _, values = _build_realized_pnl_curve(positions, now=NOW)
        assert values[0] == 100.0
        assert values[-1] == pytest.approx(250.0)

    def test_excludes_outside_window(self):
        positions = [
            _pos(days_ago=120, pnl=500, won=True),
            _pos(days_ago=5, pnl=100, won=True),
        ]
        _, values = _build_realized_pnl_curve(positions, now=NOW, window_days=90)
        assert values[-1] == 100.0


# ─────────────────────────────────────────────────────────────────────────────
# Feature integration (via ScanContext + real pre_reg)
# ─────────────────────────────────────────────────────────────────────────────

class TestSteadyGrowthFeature:
    def test_insufficient_resolved_returns_low_samples(self):
        positions = [_pos(days_ago=10, pnl=100, won=True) for _ in range(5)]
        result = SteadyGrowthFeature().compute(_ctx(positions))
        assert result.confidence == "low_samples"
        assert result.value["is_steady_grower"] is False
        assert result.value["reason"] == "insufficient_resolved"

    def test_smooth_monotonic_curve_fires(self):
        # 40 winning positions spread across 80 days, each +$200 → monotonic
        positions = [
            _pos(
                days_ago=80 - i * 2,
                pnl=200,
                won=True,
                condition_id=f"0x{i:02d}",
                initial_value=500,
            )
            for i in range(40)
        ]
        result = SteadyGrowthFeature().compute(_ctx(positions))
        assert result.confidence == "ok"
        v = result.value
        assert v["is_steady_grower"] is True, f"checks={v['checks']}"
        assert v["components"]["r_squared"] >= 0.95
        assert v["max_drawdown_ratio"] == 0.0
        assert v["longest_losing_streak"] == 0

    def test_volatile_curve_fails(self):
        # Alternating wins and big losses → high drawdown, low smoothness
        positions = []
        for i in range(40):
            won = i % 2 == 0
            positions.append(
                _pos(
                    days_ago=80 - i * 2,
                    pnl=200 if won else -400,
                    won=won,
                    condition_id=f"0x{i:02d}",
                    initial_value=500,
                )
            )
        result = SteadyGrowthFeature().compute(_ctx(positions))
        assert result.confidence == "ok"
        assert result.value["is_steady_grower"] is False

    def test_long_losing_streak_fails(self):
        # 35 wins then 6 losses at the end → smoothness degrades + streak fails
        positions = [
            _pos(
                days_ago=80 - i * 2,
                pnl=200,
                won=True,
                condition_id=f"0x{i:02d}",
                initial_value=500,
            )
            for i in range(35)
        ]
        # Append 6 recent losses
        for i in range(6):
            positions.append(
                _pos(
                    days_ago=5 - i * 0 + i,
                    pnl=-300,
                    won=False,
                    condition_id=f"0xL{i}",
                    initial_value=500,
                )
            )
        result = SteadyGrowthFeature().compute(_ctx(positions))
        v = result.value
        assert v["longest_losing_streak"] >= 5
        # Either streak fails or segments_positive fails — either way, not steady
        assert v["is_steady_grower"] is False

    def test_result_structure_has_all_keys(self):
        positions = [
            _pos(
                days_ago=80 - i * 2,
                pnl=100,
                won=True,
                condition_id=f"0x{i:02d}",
                initial_value=500,
            )
            for i in range(40)
        ]
        result = SteadyGrowthFeature().compute(_ctx(positions))
        v = result.value
        assert set(v.keys()) >= {
            "is_steady_grower",
            "smoothness_score",
            "components",
            "max_drawdown_ratio",
            "max_drawdown_amount_usdc",
            "longest_losing_streak",
            "segment_pnls_usdc",
            "all_segments_positive",
            "cumulative_pnl_usdc",
            "curve_days",
            "checks",
        }
        assert set(v["components"].keys()) == {
            "r_squared",
            "gain_to_pain_ratio",
            "gain_to_pain_normalized",
            "new_high_frequency_30d",
        }
        assert set(v["checks"].keys()) == {
            "smoothness",
            "drawdown",
            "losing_streak",
            "segments_positive",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Registry + scanner integration
# ─────────────────────────────────────────────────────────────────────────────

class TestRegistryIntegration:
    def test_feature_in_registry(self):
        from polymarket.scanner.features import REGISTRY

        assert "steady_growth" in REGISTRY
        assert REGISTRY["steady_growth"].version == "1.0"

    def test_enabled_in_current_scanner_version(self):
        from polymarket.scanner import SCANNER_VERSION

        pre_reg = load_pre_registered()
        enabled = pre_reg["scanner"]["features"]["enabled_in_version"][SCANNER_VERSION]
        assert "steady_growth" in enabled

    def test_scan_wallet_produces_steady_growth(self):
        from polymarket.scanner.scan import scan_wallet

        positions = [
            _pos(
                days_ago=80 - i * 2,
                pnl=200,
                won=True,
                condition_id=f"0x{i:02d}",
                initial_value=500,
            )
            for i in range(40)
        ]
        trades = [_trade(days_ago=70 - i, tid=f"t{i}") for i in range(30)]
        profile = scan_wallet("0xw", trades, positions, now=NOW)
        assert "steady_growth" in profile.features
        sg = profile.features["steady_growth"]
        assert sg.confidence == "ok"
        assert sg.value["is_steady_grower"] is True
