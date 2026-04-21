"""Tests for polymarket.features.whales — tier classification + stability filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.features.whales import (
    TIER_EXCLUDED,
    TIER_VOLATILE,
    _check_stability,
    _compute_segment_win_rates,
    classify_tier,
    classify_wallet,
    compute_whale_stats,
)
from polymarket.models import Position, Trade


NOW = datetime(2026, 4, 21, 12, 0, tzinfo=timezone.utc)


def _trade(days_ago: int, notional: float = 500.0, trade_id: str = "t") -> Trade:
    price = Decimal("0.5")
    size = Decimal(str(notional / 0.5))
    return Trade(
        id=f"{trade_id}:{days_ago}",
        market="0xA",
        asset_id="tok1",
        price=price,
        size=size,
        side="BUY",
        match_time=NOW - timedelta(days=days_ago),
    )


def _position(*, days_ago: int, pnl: float, won: bool, size: float = 100.0) -> Position:
    return Position(
        proxyWallet="0xwallet",
        asset="tok1",
        conditionId="0xA",
        outcome="Yes",
        size=Decimal(str(size)),
        avgPrice=Decimal("0.5"),
        initialValue=Decimal(str(size * 0.5)),
        currentValue=Decimal(str(size if won else 0)),
        cashPnl=Decimal(str(pnl)),
        curPrice=Decimal("1" if won else "0"),
        redeemable=True,
        endDate=NOW - timedelta(days=days_ago),
    )


class TestComputeWhaleStats:
    def test_empty_inputs_return_zero(self):
        s = compute_whale_stats("0x1", [], [], now=NOW)
        assert s.trade_count_90d == 0
        assert s.win_rate == 0.0
        assert s.cumulative_pnl == 0.0
        assert s.avg_trade_size == 0.0

    def test_filters_trades_older_than_90d(self):
        trades = [_trade(days_ago=30, trade_id="a"), _trade(days_ago=120, trade_id="b")]
        s = compute_whale_stats("0x1", trades, [], now=NOW)
        assert s.trade_count_90d == 1

    def test_avg_trade_size(self):
        trades = [_trade(10, notional=1000), _trade(20, notional=500, trade_id="x")]
        s = compute_whale_stats("0x1", trades, [], now=NOW)
        assert s.avg_trade_size == pytest.approx(750.0)

    def test_cumulative_pnl_from_realized(self):
        positions = [
            _position(days_ago=5, pnl=100, won=True),
            _position(days_ago=10, pnl=-50, won=False),
        ]
        s = compute_whale_stats("0x1", [], positions, now=NOW)
        assert s.cumulative_pnl == pytest.approx(50.0)

    def test_win_rate_only_counts_resolved(self):
        resolved_win = _position(days_ago=5, pnl=100, won=True)
        resolved_loss = _position(days_ago=10, pnl=-50, won=False)
        open_pos = Position(
            proxyWallet="0xw",
            conditionId="0xB",
            outcome="Yes",
            size=Decimal("1"),
            initialValue=Decimal("0.5"),
            curPrice=Decimal("0.45"),  # not 0 or 1
            redeemable=False,  # unresolved
        )
        s = compute_whale_stats("0x1", [], [resolved_win, resolved_loss, open_pos], now=NOW)
        assert s.resolved_count == 2
        assert s.win_rate == 0.5


class TestSegmentWinRates:
    def test_three_segments_populated(self):
        positions = [
            # Segment 0 (last 30d): 2 wins 0 losses (insufficient — needs ≥3)
            _position(days_ago=5, pnl=10, won=True),
            _position(days_ago=10, pnl=10, won=True),
            # Segment 1 (30-60d): 3 positions, 2 wins
            _position(days_ago=35, pnl=10, won=True),
            _position(days_ago=40, pnl=10, won=True),
            _position(days_ago=45, pnl=-10, won=False),
            # Segment 2 (60-90d): 4 positions, 3 wins
            _position(days_ago=65, pnl=10, won=True),
            _position(days_ago=70, pnl=10, won=True),
            _position(days_ago=75, pnl=-10, won=False),
            _position(days_ago=85, pnl=10, won=True),
        ]
        rates = _compute_segment_win_rates(positions, now=NOW)
        assert len(rates) == 3
        assert rates[0] == -1.0  # insufficient samples
        assert rates[1] == pytest.approx(2 / 3)
        assert rates[2] == pytest.approx(3 / 4)

    def test_out_of_range_positions_excluded(self):
        positions = [_position(days_ago=150, pnl=10, won=True)]
        rates = _compute_segment_win_rates(positions, now=NOW)
        assert all(r == -1.0 for r in rates)


class TestCheckStability:
    def test_all_segments_pass(self):
        assert _check_stability([0.6, 0.55, 0.58], tier_min_win_rate=0.6, ratio=0.85) is True

    def test_one_segment_below_threshold_fails(self):
        # 0.6 * 0.85 = 0.51; 0.50 < 0.51
        assert _check_stability([0.6, 0.55, 0.50], tier_min_win_rate=0.6, ratio=0.85) is False

    def test_sentinel_segment_fails(self):
        assert _check_stability([0.6, -1.0, 0.55], tier_min_win_rate=0.6, ratio=0.85) is False

    def test_too_few_segments_fails(self):
        assert _check_stability([0.6, 0.7], tier_min_win_rate=0.6, ratio=0.85) is False


class TestClassifyTier:
    def _make_passing_stats(self, tier: str):
        from polymarket.features.whales import WhaleStats

        thresholds = {
            "A": (20, 0.60, 10000, 500),
            "B": (15, 0.55, 5000, 250),
            "C": (10, 0.50, 2000, 100),
        }
        trades, win, pnl, avg = thresholds[tier]
        return WhaleStats(
            wallet_address="0x1",
            trade_count_90d=trades,
            win_rate=win,
            cumulative_pnl=pnl,
            avg_trade_size=avg,
            resolved_count=20,
            segment_win_rates=[win * 0.9, win * 0.9, win * 0.9],
        )

    def test_classifies_as_a_when_meeting_a_thresholds(self):
        stats = self._make_passing_stats("A")
        tier = classify_tier(stats)
        assert tier == "A"
        assert stats.stability_pass is True

    def test_classifies_as_b_when_meeting_b_but_not_a(self):
        stats = self._make_passing_stats("B")
        assert classify_tier(stats) == "B"

    def test_classifies_as_c_when_meeting_c_only(self):
        stats = self._make_passing_stats("C")
        assert classify_tier(stats) == "C"

    def test_stability_failure_returns_volatile(self):
        stats = self._make_passing_stats("A")
        stats.segment_win_rates = [0.3, 0.9, 0.9]  # 第一段遠低於 0.60 * 0.85 = 0.51
        assert classify_tier(stats) == TIER_VOLATILE
        assert stats.stability_pass is False

    def test_below_all_thresholds_excluded(self):
        from polymarket.features.whales import WhaleStats

        stats = WhaleStats(wallet_address="0x1", trade_count_90d=5, win_rate=0.3)
        assert classify_tier(stats) == TIER_EXCLUDED


class TestClassifyWalletEndToEnd:
    def test_a_tier_wallet_with_stability(self):
        # 30 trades at $800 (avg > 500), spread across 3 segments
        trades = [_trade(days_ago=i * 3, notional=800, trade_id=f"t{i}") for i in range(30)]
        # 30 resolved positions, each $500 realized pnl win / $200 loss, 70% win rate
        # Distribute evenly across 3 segments (days 0-30, 30-60, 60-90)
        positions = []
        for seg in range(3):
            for i in range(10):
                days_ago = seg * 30 + 5 + i * 2  # within segment
                won = i < 7  # 70% per segment
                pnl = 500 if won else -200
                positions.append(_position(days_ago=days_ago, pnl=pnl, won=won))
        stats = classify_wallet("0x1", trades, positions, now=NOW)

        # Verify computed statistics
        assert stats.trade_count_90d == 30
        assert stats.win_rate == pytest.approx(0.7, abs=0.01)
        # 21 wins × $500 + 9 losses × -$200 = 10500 - 1800 = 8700 (不達 $10k 門檻)
        assert stats.cumulative_pnl == pytest.approx(8700.0)
        assert stats.avg_trade_size == 800.0
        # 勝率 70% 過 A（60%），但 cumulative_pnl $8700 < $10k 門檻
        # 所以應落到 B（門檻 $5000）— 只要三段勝率一致，會通過穩定性
        assert stats.tier == "B"
        assert stats.stability_pass is True

    def test_volatile_when_stability_fails(self):
        # 高績效但集中在最近 30 天，前兩段樣本不足或勝率低
        trades = [_trade(days_ago=i, notional=800, trade_id=f"t{i}") for i in range(30)]
        positions = []
        # 最近 30 天：10 個倉位，全贏
        for i in range(10):
            positions.append(_position(days_ago=5 + i, pnl=500, won=True))
        # 30-60 天：3 個倉位，全輸（勝率 0）
        for i in range(3):
            positions.append(_position(days_ago=35 + i, pnl=-200, won=False))
        # 60-90 天：樣本不足
        for i in range(2):
            positions.append(_position(days_ago=65 + i, pnl=500, won=True))
        stats = classify_wallet("0x1", trades, positions, now=NOW)
        # 整體勝率 12/15 = 80%，滿足門檻；但段 1 勝率 0，段 2 樣本不足
        assert stats.tier == TIER_VOLATILE
