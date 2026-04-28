"""Concrete guard implementations for risk control.

All guards are synchronous (no async) to avoid fragile event loop issues
inside Freqtrade's runtime.
"""

from __future__ import annotations

import os
import time
from typing import Optional

from guards.base import Guard, GuardContext


class EntryRateGuard(Guard):
    """Wall-clock circuit breaker on entry frequency.

    Freqtrade's ``max_open_trades`` caps simultaneous positions but cannot
    bound aggregate entry frequency over wall-clock time. The 04-26 burst
    incident produced 93 entries in 1 hour while never violating
    ``max_open_trades=3`` instantaneously (positions cycled fast). This
    guard adds the missing dimension: at most ``max_per_hour`` accepted
    entries within any rolling ``window_s`` seconds.

    State is in-memory only (not persisted via guards.pipeline.save_state).
    Container restart resets the window — intentional, so deploys aren't
    flagged as bursts.

    Caller responsibility: invoke ``record_entry()`` AFTER the entry
    actually goes through (after all guards pass and the strategy returns
    True from confirm_trade_entry). Mirrors CooldownGuard.record_trade.
    """

    def __init__(self, max_per_hour: int = 5, window_s: int = 3600):
        env_val = os.environ.get("SUPERTREND_MAX_ENTRIES_PER_HOUR")
        self.max_per_hour = int(env_val) if env_val else int(max_per_hour)
        self.window_s = int(window_s)
        self._entry_ts: list[float] = []

    def check(self, ctx: GuardContext) -> Optional[str]:
        now = time.time()
        # Prune entries older than window first so the count is accurate
        self._entry_ts = [t for t in self._entry_ts if now - t < self.window_s]
        if len(self._entry_ts) >= self.max_per_hour:
            oldest_age_min = (now - self._entry_ts[0]) / 60
            return (
                f"EntryRateGuard: {len(self._entry_ts)} entries in last "
                f"{self.window_s // 60}min (cap={self.max_per_hour}); "
                f"oldest at {oldest_age_min:.1f}min ago. Possible burst; "
                f"blocking new entries until window slides."
            )
        return None

    def record_entry(self) -> None:
        """Record an accepted entry. Caller must invoke after entry confirmed."""
        self._entry_ts.append(time.time())


class MaxPositionGuard(Guard):
    """Reject if a single position would exceed max % of account.

    Confidence-aware: when confidence >= threshold, the effective limit
    scales linearly from max_pct up to confident_pct.  This lets the
    confidence engine's high-conviction trades through while keeping a
    strict cap for low-confidence entries.
    """

    def __init__(self, max_pct: float = 30.0, confident_pct: float = 45.0,
                 confidence_threshold: float = 0.7):
        self.max_pct = max_pct
        self.confident_pct = confident_pct
        self.confidence_threshold = confidence_threshold

    def check(self, ctx: GuardContext) -> Optional[str]:
        # Dynamically raise the cap when confidence engine is highly confident
        if ctx.confidence >= self.confidence_threshold:
            denom = 1.0 - self.confidence_threshold
            t = min((ctx.confidence - self.confidence_threshold) / denom, 1.0) if denom > 0 else 1.0
            effective_pct = self.max_pct + (self.confident_pct - self.max_pct) * t
        else:
            effective_pct = self.max_pct

        position_value = ctx.amount * ctx.leverage
        max_allowed = ctx.account_balance * (effective_pct / 100)
        if position_value > max_allowed:
            return (
                f"Position value {position_value:.2f} exceeds "
                f"{effective_pct:.0f}% of account ({max_allowed:.2f})"
            )
        return None


class MaxLeverageGuard(Guard):
    """Reject if leverage exceeds dynamic maximum based on account size.

    Smaller accounts get lower leverage limits to prevent rapid ruin:
    $300 → max 2.5x, $500 → 3.3x, $1000+ → full max_leverage.
    """

    def __init__(self, max_leverage: float = 5.0):
        self.max_leverage = max_leverage

    def check(self, ctx: GuardContext) -> Optional[str]:
        # Dynamic leverage: scale with account size (smaller = more conservative)
        size_factor = min(ctx.account_balance / 1000, 1.0)
        dynamic_max = 1.5 + (self.max_leverage - 1.5) * size_factor
        if ctx.leverage > dynamic_max:
            return (
                f"Leverage {ctx.leverage:.1f}x exceeds {dynamic_max:.1f}x "
                f"for ${ctx.account_balance:.0f} account"
            )
        return None


class CooldownGuard(Guard):
    """Reject if trading the same symbol within cooldown period."""

    def __init__(self, minutes: int = 15):
        self.cooldown_seconds = minutes * 60
        self._last_trade: dict[str, float] = {}

    def record_trade(self, symbol: str) -> None:
        self._last_trade[symbol] = time.time()

    def check(self, ctx: GuardContext) -> Optional[str]:
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

    def check(self, ctx: GuardContext) -> Optional[str]:
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

    def check(self, ctx: GuardContext) -> Optional[str]:
        if time.time() < self._paused_until:
            remaining_h = (self._paused_until - time.time()) / 3600
            return (
                f"Trading paused for {remaining_h:.1f}h "
                f"after {self.max_streak} consecutive losses"
            )
        return None


class TotalExposureGuard(Guard):
    """Reject if total portfolio exposure across all positions exceeds limit.

    Prevents correlated exposure across multiple pairs.
    """

    def __init__(self, max_pct: float = 80.0):
        self.max_pct = max_pct

    def check(self, ctx: GuardContext) -> Optional[str]:
        # Sum existing position values + proposed new position
        existing_exposure = sum(
            float(pos.get("value", 0))
            for pos in ctx.open_positions.values()
        )
        new_exposure = ctx.amount * ctx.leverage
        total = existing_exposure + new_exposure
        max_allowed = ctx.account_balance * (self.max_pct / 100)

        if total > max_allowed:
            return (
                f"Total exposure {total:.2f} would exceed "
                f"{self.max_pct}% of account ({max_allowed:.2f})"
            )
        return None


class DrawdownGuard(Guard):
    """Reject new entries when portfolio drawdown from peak exceeds threshold.

    Tracks equity peak and blocks trading when current equity drops below
    (1 - max_drawdown_pct/100) * peak. Resets peak on new highs.
    """

    def __init__(self, max_drawdown_pct: float = 10.0):
        self.max_drawdown_pct = max_drawdown_pct
        self._peak_equity: float = 0.0

    def update_equity(self, equity: float) -> None:
        """Call on every bot loop to track equity peak."""
        if equity > self._peak_equity:
            self._peak_equity = equity

    def check(self, ctx: GuardContext) -> Optional[str]:
        # Initialize peak from account balance if not set
        if self._peak_equity <= 0:
            self._peak_equity = ctx.account_balance

        if self._peak_equity <= 0:
            return None

        drawdown_pct = (1.0 - ctx.account_balance / self._peak_equity) * 100
        if drawdown_pct >= self.max_drawdown_pct:
            return (
                f"Portfolio drawdown {drawdown_pct:.1f}% exceeds "
                f"{self.max_drawdown_pct}% limit "
                f"(peak: {self._peak_equity:.2f}, current: {ctx.account_balance:.2f})"
            )
        return None


class LiquidationGuard(Guard):
    """Reject entries where liquidation price is too close to entry.

    Ensures liquidation distance is at least `min_distance_mult` times
    the stop-loss distance, providing a safety buffer against flash crashes.

    OKX isolated margin maintenance rates by tier:
    - Tier 1 (≤$10k): ~1.0%
    - Tier 2 (≤$50k): ~1.5%
    - Tier 3 (≤$200k): ~2.0%
    Default 1.0% is conservative for small accounts.
    """

    def __init__(self, min_distance_mult: float = 2.0, maintenance_margin_rate: float = 0.01):
        self.min_distance_mult = min_distance_mult
        self.maintenance_margin_rate = maintenance_margin_rate

    def check(self, ctx: GuardContext) -> Optional[str]:
        if ctx.leverage <= 1.0:
            return None

        # Estimate liquidation distance as fraction of entry price
        # liq_distance ≈ (1 / leverage) - maintenance_margin_rate
        liq_distance_pct = (1.0 / ctx.leverage) - self.maintenance_margin_rate
        if liq_distance_pct <= 0:
            return (
                f"Leverage {ctx.leverage}x too high: liquidation distance is negative "
                f"(maintenance margin rate: {self.maintenance_margin_rate})"
            )

        # Typical stop-loss is 3-5% — use a conservative 5% as reference
        stoploss_pct = getattr(ctx, "stoploss_pct", 0.05)

        if liq_distance_pct < stoploss_pct * self.min_distance_mult:
            return (
                f"Liquidation distance {liq_distance_pct:.1%} is less than "
                f"{self.min_distance_mult}x stop-loss ({stoploss_pct:.1%}). "
                f"Reduce leverage from {ctx.leverage}x"
            )
        return None


class DirectionalExposureGuard(Guard):
    """Reduce TotalExposure limit when all open positions share the same direction.

    Crypto assets are highly correlated (BTC-ETH ~0.82, BTC-SOL ~0.75).
    When all positions are long (or all short), a market-wide move amplifies
    losses across the portfolio. This guard tightens the overall exposure
    ceiling when directional concentration is detected.

    Example: 3 long positions → reduce max exposure from 80% to 60%.
    """

    def __init__(self, max_same_dir: int = 2, reduced_pct: float = 60.0, normal_pct: float = 80.0):
        self.max_same_dir = max_same_dir
        self.reduced_pct = reduced_pct
        self.normal_pct = normal_pct

    def check(self, ctx: GuardContext) -> Optional[str]:
        if not ctx.open_positions:
            return None

        # Count positions in same direction as new trade
        same_dir_count = 0
        for pos in ctx.open_positions.values():
            pos_side = pos.get("side", "long")
            if pos_side == ctx.side:
                same_dir_count += 1

        if same_dir_count >= self.max_same_dir:
            # Tighter exposure limit
            existing_exposure = sum(
                float(pos.get("value", 0))
                for pos in ctx.open_positions.values()
            )
            new_exposure = ctx.amount * ctx.leverage
            total = existing_exposure + new_exposure
            max_allowed = ctx.account_balance * (self.reduced_pct / 100)

            if total > max_allowed:
                return (
                    f"Directional concentration: {same_dir_count + 1} {ctx.side} positions, "
                    f"total exposure {total:.2f} exceeds reduced "
                    f"{self.reduced_pct}% limit ({max_allowed:.2f})"
                )
        return None
