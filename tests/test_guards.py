"""Tests for the guard pipeline."""


import pytest

from guards.base import GuardContext, GuardPipeline
from guards.guards import (
    ConsecutiveLossGuard,
    CooldownGuard,
    DailyLossGuard,
    MaxLeverageGuard,
    MaxPositionGuard,
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


@pytest.mark.asyncio
async def test_max_position_pass():
    guard = MaxPositionGuard(max_pct=30)
    ctx = make_ctx(amount=100, leverage=2)  # 200 < 300 (30% of 1000)
    assert await guard.check(ctx) is None


@pytest.mark.asyncio
async def test_max_position_reject():
    guard = MaxPositionGuard(max_pct=30)
    ctx = make_ctx(amount=200, leverage=3)  # 600 > 300
    result = await guard.check(ctx)
    assert result is not None
    assert "exceeds" in result


@pytest.mark.asyncio
async def test_max_leverage_pass():
    guard = MaxLeverageGuard(max_leverage=5)
    ctx = make_ctx(leverage=3)
    assert await guard.check(ctx) is None


@pytest.mark.asyncio
async def test_max_leverage_reject():
    guard = MaxLeverageGuard(max_leverage=5)
    ctx = make_ctx(leverage=10)
    result = await guard.check(ctx)
    assert result is not None
    assert "10" in result


@pytest.mark.asyncio
async def test_cooldown_pass():
    guard = CooldownGuard(minutes=1)
    ctx = make_ctx()
    assert await guard.check(ctx) is None


@pytest.mark.asyncio
async def test_cooldown_reject():
    guard = CooldownGuard(minutes=60)
    guard.record_trade("BTC/USDT:USDT")
    ctx = make_ctx()
    result = await guard.check(ctx)
    assert result is not None
    assert "cooldown" in result


@pytest.mark.asyncio
async def test_daily_loss_pass():
    guard = DailyLossGuard(max_pct=5)
    ctx = make_ctx()
    assert await guard.check(ctx) is None


@pytest.mark.asyncio
async def test_daily_loss_reject():
    guard = DailyLossGuard(max_pct=5)
    guard.record_loss(50)  # 50 = 5% of 1000
    ctx = make_ctx()
    result = await guard.check(ctx)
    assert result is not None
    assert "limit" in result


@pytest.mark.asyncio
async def test_consecutive_loss_pass():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    guard.record_result(False)
    guard.record_result(False)
    ctx = make_ctx()
    assert await guard.check(ctx) is None  # 2 < 3


@pytest.mark.asyncio
async def test_consecutive_loss_reject():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    for _ in range(3):
        guard.record_result(False)
    ctx = make_ctx()
    result = await guard.check(ctx)
    assert result is not None
    assert "paused" in result


@pytest.mark.asyncio
async def test_consecutive_loss_reset_on_win():
    guard = ConsecutiveLossGuard(max_streak=3, pause_hours=1)
    guard.record_result(False)
    guard.record_result(False)
    guard.record_result(True)  # Reset streak
    guard.record_result(False)
    ctx = make_ctx()
    assert await guard.check(ctx) is None  # streak = 1


@pytest.mark.asyncio
async def test_pipeline_all_pass():
    pipeline = GuardPipeline([
        MaxPositionGuard(max_pct=50),
        MaxLeverageGuard(max_leverage=10),
    ])
    ctx = make_ctx(amount=100, leverage=3)
    assert await pipeline.run(ctx) is None


@pytest.mark.asyncio
async def test_pipeline_first_rejection():
    pipeline = GuardPipeline([
        MaxLeverageGuard(max_leverage=2),  # Will reject
        MaxPositionGuard(max_pct=50),      # Won't be reached
    ])
    ctx = make_ctx(leverage=5)
    result = await pipeline.run(ctx)
    assert result is not None
    assert "MaxLeverageGuard" in result
