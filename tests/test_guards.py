"""Tests for the guard pipeline."""


import pytest

from guards.base import GuardContext, GuardPipeline
from guards.guards import (
    ConsecutiveLossGuard,
    CooldownGuard,
    DailyLossGuard,
    MaxLeverageGuard,
    MaxPositionGuard,
    TotalExposureGuard,
)


def make_ctx(**overrides) -> GuardContext:
    defaults = {
        "symbol": "BTC/USDT:USDT",
        "side": "long",
        "amount": 100.0,
        "leverage": 3.0,
        "account_balance": 1000.0,
    }
    defaults.update(overrides)
    return GuardContext(**defaults)


def test_max_position_pass():
    guard = MaxPositionGuard(max_pct=30)
    ctx = make_ctx(amount=100, leverage=2)  # 200 < 300 (30% of 1000)
    assert guard.check(ctx) is None


def test_max_position_reject():
    guard = MaxPositionGuard(max_pct=30)
    ctx = make_ctx(amount=200, leverage=3)  # 600 > 300
    result = guard.check(ctx)
    assert result is not None
    assert "exceeds" in result


def test_max_leverage_pass():
    guard = MaxLeverageGuard(max_leverage=5)
    ctx = make_ctx(leverage=3)
    assert guard.check(ctx) is None


def test_max_leverage_reject():
    guard = MaxLeverageGuard(max_leverage=5)
    ctx = make_ctx(leverage=10)
    result = guard.check(ctx)
    assert result is not None
    assert "10" in result


def test_cooldown_pass():
    guard = CooldownGuard(minutes=1)
    ctx = make_ctx()
    assert guard.check(ctx) is None


def test_cooldown_reject():
    guard = CooldownGuard(minutes=60)
    guard.record_trade("BTC/USDT:USDT")
    ctx = make_ctx()
    result = guard.check(ctx)
    assert result is not None
    assert "cooldown" in result


def test_daily_loss_pass():
    guard = DailyLossGuard(max_pct=5)
    ctx = make_ctx()
    assert guard.check(ctx) is None


def test_daily_loss_reject():
    guard = DailyLossGuard(max_pct=5)
    guard.record_loss(50)  # 50 = 5% of 1000
    ctx = make_ctx()
    result = guard.check(ctx)
    assert result is not None
    assert "limit" in result


def test_consecutive_loss_pass():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    guard.record_result(False)
    guard.record_result(False)
    ctx = make_ctx()
    assert guard.check(ctx) is None  # 2 < 3


def test_consecutive_loss_reject():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    for _ in range(3):
        guard.record_result(False)
    ctx = make_ctx()
    result = guard.check(ctx)
    assert result is not None
    assert "paused" in result


def test_consecutive_loss_reset_on_win():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    guard.record_result(False)
    guard.record_result(False)
    guard.record_result(True)  # Reset streak
    guard.record_result(False)
    ctx = make_ctx()
    assert guard.check(ctx) is None  # streak = 1


def test_total_exposure_pass():
    guard = TotalExposureGuard(max_pct=80)
    ctx = make_ctx(amount=100, leverage=3)  # 300 < 800
    assert guard.check(ctx) is None


def test_total_exposure_reject():
    guard = TotalExposureGuard(max_pct=80)
    ctx = make_ctx(
        amount=100, leverage=3,
        open_positions={"ETH/USDT:USDT": {"value": 600}},  # 600 + 300 = 900 > 800
    )
    result = guard.check(ctx)
    assert result is not None
    assert "Total exposure" in result


def test_pipeline_all_pass():
    pipeline = GuardPipeline([
        MaxPositionGuard(max_pct=50),
        MaxLeverageGuard(max_leverage=10),
    ])
    ctx = make_ctx(amount=100, leverage=3)
    assert pipeline.run(ctx) is None


def test_pipeline_first_rejection():
    pipeline = GuardPipeline([
        MaxLeverageGuard(max_leverage=2),  # Will reject
        MaxPositionGuard(max_pct=50),      # Won't be reached
    ])
    ctx = make_ctx(leverage=5)
    result = pipeline.run(ctx)
    assert result is not None
    assert "MaxLeverageGuard" in result
