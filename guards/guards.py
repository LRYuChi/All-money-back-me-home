"""Concrete guard implementations for risk control."""

from __future__ import annotations

import time
from typing import Optional

from guards.base import Guard, GuardContext


class MaxPositionGuard(Guard):
    """Reject if a single position would exceed max % of account."""

    def __init__(self, max_pct: float = 30.0):
        self.max_pct = max_pct

    async def check(self, ctx: GuardContext) -> Optional[str]:
        position_value = ctx.amount * ctx.leverage
        max_allowed = ctx.account_balance * (self.max_pct / 100)
        if position_value > max_allowed:
            return (
                f"Position value {position_value:.2f} exceeds "
                f"{self.max_pct}% of account ({max_allowed:.2f})"
            )
        return None


class MaxLeverageGuard(Guard):
    """Reject if leverage exceeds maximum."""

    def __init__(self, max_leverage: float = 5.0):
        self.max_leverage = max_leverage

    async def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.leverage > self.max_leverage:
            return f"Leverage {ctx.leverage}x exceeds max {self.max_leverage}x"
        return None


class CooldownGuard(Guard):
    """Reject if trading the same symbol within cooldown period."""

    def __init__(self, minutes: int = 15):
        self.cooldown_seconds = minutes * 60
        self._last_trade: dict[str, float] = {}

    def record_trade(self, symbol: str) -> None:
        self._last_trade[symbol] = time.time()

    async def check(self, ctx: GuardContext) -> Optional[str]:
        last = self._last_trade.get(ctx.symbol)
        if last is not None:
            elapsed = time.time() - last
            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed)
                return f"{ctx.symbol} cooldown: {remaining}s remaining"
        return None


class DailyLossGuard(Guard):
    """Reject if daily realized loss exceeds max % of account."""

    def __init__(self, max_pct: float = 5.0):
        self.max_pct = max_pct
        self._daily_loss: float = 0.0
        self._reset_day: str = ""

    def record_loss(self, amount: float) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._reset_day:
            self._daily_loss = 0.0
            self._reset_day = today
        self._daily_loss += abs(amount)

    async def check(self, ctx: GuardContext) -> Optional[str]:
        today = time.strftime("%Y-%m-%d")
        if today != self._reset_day:
            self._daily_loss = 0.0
            self._reset_day = today

        max_loss = ctx.account_balance * (self.max_pct / 100)
        if self._daily_loss >= max_loss:
            return (
                f"Daily loss {self._daily_loss:.2f} reached "
                f"{self.max_pct}% limit ({max_loss:.2f})"
            )
        return None


class ConsecutiveLossGuard(Guard):
    """Auto-pause trading after N consecutive losses."""

    def __init__(self, max_streak: int = 5, pause_hours: int = 24):
        self.max_streak = max_streak
        self.pause_seconds = pause_hours * 3600
        self._streak: int = 0
        self._paused_until: float = 0

    def record_result(self, is_win: bool) -> None:
        if is_win:
            self._streak = 0
        else:
            self._streak += 1
            if self._streak >= self.max_streak:
                self._paused_until = time.time() + self.pause_seconds

    async def check(self, ctx: GuardContext) -> Optional[str]:
        if time.time() < self._paused_until:
            remaining_h = (self._paused_until - time.time()) / 3600
            return (
                f"Trading paused for {remaining_h:.1f}h "
                f"after {self.max_streak} consecutive losses"
            )
        return None
