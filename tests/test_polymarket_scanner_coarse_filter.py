"""Tests for polymarket.scanner.coarse_filter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from polymarket.config import load_pre_registered
from polymarket.models import Position, Trade
from polymarket.scanner.coarse_filter import _market_concentration, apply_coarse_filter

NOW = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)


def _trade(market: str = "0xA", days_ago: int = 1, notional: float = 500.0) -> Trade:
    price = Decimal("0.5")
    size = Decimal(str(notional / 0.5))
    return Trade(
        id=f"t-{market}-{days_ago}",
        market=market,
        asset_id="tok1",
        price=price,
        size=size,
        side="BUY",
        match_time=NOW - timedelta(days=days_ago),
    )


def _resolved_position(pnl: float) -> Position:
    return Position(
        proxyWallet="0xw",
        conditionId="0xA",
        outcome="Yes",
        size=Decimal("100"),
        cashPnl=Decimal(str(pnl)),
        curPrice=Decimal("1") if pnl > 0 else Decimal("0"),
        initialValue=Decimal("50"),
        redeemable=True,
    )


@pytest.fixture
def pre_reg():
    return load_pre_registered()


class TestApplyCoarseFilter:
    def test_passes_with_normal_wallet(self, pre_reg):
        trades = [_trade(market=f"0x{i}", days_ago=i) for i in range(10)]
        positions = [_resolved_position(100) for _ in range(3)]
        result = apply_coarse_filter("0xw", trades, positions, pre_reg, now=NOW)
        assert result.passed is True
        assert result.reasons == []

    def test_fails_on_too_few_trades(self, pre_reg):
        trades = [_trade(days_ago=1)]  # 1 < 5
        result = apply_coarse_filter("0xw", trades, [], pre_reg, now=NOW)
        assert result.passed is False
        assert any("insufficient_trades" in r for r in result.reasons)

    def test_fails_on_no_trades(self, pre_reg):
        result = apply_coarse_filter("0xw", [], [], pre_reg, now=NOW)
        assert result.passed is False
        assert "no_trades" in result.reasons

    def test_fails_on_stale_activity(self, pre_reg):
        trades = [_trade(market=f"0x{i}", days_ago=60) for i in range(10)]  # all > 30d
        result = apply_coarse_filter("0xw", trades, [], pre_reg, now=NOW)
        assert result.passed is False
        assert any("stale_activity" in r for r in result.reasons)

    def test_fails_on_negative_pnl(self, pre_reg):
        trades = [_trade(market=f"0x{i}", days_ago=i) for i in range(10)]
        positions = [_resolved_position(-2000)]  # < -1000 threshold
        result = apply_coarse_filter("0xw", trades, positions, pre_reg, now=NOW)
        assert result.passed is False
        assert any("negative_pnl" in r for r in result.reasons)

    def test_fails_on_market_maker_concentration(self, pre_reg):
        # All 10 trades on same market = 100% concentration > 50% threshold
        trades = [_trade(market="0xA", days_ago=i) for i in range(10)]
        result = apply_coarse_filter("0xw", trades, [], pre_reg, now=NOW)
        assert result.passed is False
        assert any("market_maker_concentration" in r for r in result.reasons)

    def test_collects_multiple_reasons(self, pre_reg):
        trades = [_trade(days_ago=60)]  # too few + stale + concentrated
        result = apply_coarse_filter("0xw", trades, [], pre_reg, now=NOW)
        assert result.passed is False
        assert len(result.reasons) >= 2  # multiple reasons reported


class TestMarketConcentration:
    def test_single_market_returns_one(self):
        trades = [_trade(market="0xA") for _ in range(5)]
        assert _market_concentration(trades) == 1.0

    def test_evenly_split_returns_proper_share(self):
        trades = [_trade(market="0xA"), _trade(market="0xB")]
        assert _market_concentration(trades) == 0.5

    def test_empty_returns_zero(self):
        assert _market_concentration([]) == 0.0
