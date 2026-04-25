"""Built-in guards (G1, G3, G4, G5, G6 — round 18; G8 — round 20)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from execution.pending_orders.types import PendingOrder
from risk.guards import GuardContext, GuardDecision, GuardResult


# ================================================================== #
# G1 LatencyBudget
# ================================================================== #
@dataclass(slots=True, frozen=True)
class LatencyBudgetGuard:
    """G1: signal latency budget. Stale signals get DENIED (the alpha is
    likely already priced in by now). Budget per-strategy or global."""

    name: str = "latency"
    budget_seconds: float = 15.0

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        age = ctx.signal_age_seconds
        if age is None:
            # No age info → fail open (let through). This is a deliberate
            # choice: rather than block all orders when signal age tracking
            # isn't wired, we let them through and rely on the daemon's
            # other safeguards. Caller can swap this guard out if needed.
            return GuardDecision(self.name, GuardResult.ALLOW,
                                 reason="no signal_age_seconds; pass-through")
        if age > self.budget_seconds:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=f"signal {age:.1f}s old > budget {self.budget_seconds:.1f}s",
                detail={"age_seconds": age, "budget_seconds": self.budget_seconds},
            )
        return GuardDecision(self.name, GuardResult.ALLOW,
                             detail={"age_seconds": age})


# ================================================================== #
# G3 MinSize
# ================================================================== #
@dataclass(slots=True, frozen=True)
class MinSizeGuard:
    """G3: deny dust orders below exchange minimum. Per-symbol overrides
    via min_by_symbol; falls back to default."""

    name: str = "min_size"
    default_min_usd: float = 10.0
    min_by_symbol: dict[str, float] = field(default_factory=dict)

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        minimum = self.min_by_symbol.get(order.symbol, self.default_min_usd)
        if order.target_notional_usd < minimum:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=f"notional ${order.target_notional_usd:.2f} below min ${minimum:.2f}",
                detail={"notional": order.target_notional_usd, "min": minimum},
            )
        return GuardDecision(self.name, GuardResult.ALLOW,
                             detail={"notional": order.target_notional_usd, "min": minimum})


# ================================================================== #
# G4 PerStrategyExposure
# ================================================================== #
@dataclass(slots=True, frozen=True)
class PerStrategyExposureGuard:
    """G4: cap notional per strategy.

    `cap_pct_of_capital` = 0.20 means single strategy's open notional may
    not exceed 20% of capital. If new order would breach, scale down to
    fit (with a 10% absolute floor — below that floor, deny).
    """

    name: str = "strategy_exposure"
    cap_pct_of_capital: float = 0.20
    deny_floor_pct: float = 0.10  # if scaled size < this fraction of original, deny

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        cap_usd = ctx.capital_usd * self.cap_pct_of_capital
        current = ctx.open_notional_by_strategy.get(order.strategy_id, 0.0)
        room = cap_usd - current

        if room <= 0:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(f"strategy {order.strategy_id} at cap "
                        f"(open ${current:.2f} >= cap ${cap_usd:.2f})"),
                detail={"open": current, "cap": cap_usd, "request": order.target_notional_usd},
            )

        if order.target_notional_usd <= room:
            return GuardDecision(self.name, GuardResult.ALLOW,
                                 detail={"open": current, "cap": cap_usd,
                                         "request": order.target_notional_usd})

        # Need to scale down to fit
        scaled = room
        floor = order.target_notional_usd * self.deny_floor_pct
        if scaled < floor:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(f"strategy room ${room:.2f} below {self.deny_floor_pct:.0%} "
                        f"of request ${order.target_notional_usd:.2f}"),
                detail={"open": current, "cap": cap_usd, "scaled": scaled, "floor": floor},
            )
        return GuardDecision(
            self.name, GuardResult.SCALE,
            reason=f"scaled ${order.target_notional_usd:.2f} → ${scaled:.2f} (strategy cap)",
            scaled_size_usd=scaled,
            detail={"open": current, "cap": cap_usd,
                    "original": order.target_notional_usd, "scaled": scaled},
        )


# ================================================================== #
# G5 PerMarketExposure
# ================================================================== #
@dataclass(slots=True, frozen=True)
class PerMarketExposureGuard:
    """G5: cap aggregate notional per market (crypto / us / tw / fx).

    `cap_pct_by_market` lets each market have a different cap; e.g.
    crypto 0.5 (50% of capital) but us-stocks 0.3. Falls back to
    `default_cap_pct`.

    Market is parsed from the symbol prefix: 'crypto:OKX:BTC...' → 'crypto'.
    """

    name: str = "market_exposure"
    default_cap_pct: float = 0.50
    cap_pct_by_market: dict[str, float] = field(default_factory=dict)

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        market = _market_from_symbol(order.symbol)
        cap_pct = self.cap_pct_by_market.get(market, self.default_cap_pct)
        cap_usd = ctx.capital_usd * cap_pct
        current = ctx.open_notional_by_market.get(market, 0.0)
        room = cap_usd - current

        if room <= 0:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=f"market {market} at cap (open ${current:.2f} >= cap ${cap_usd:.2f})",
                detail={"market": market, "open": current, "cap": cap_usd},
            )
        if order.target_notional_usd <= room:
            return GuardDecision(self.name, GuardResult.ALLOW,
                                 detail={"market": market, "cap": cap_usd, "open": current})

        return GuardDecision(
            self.name, GuardResult.SCALE,
            reason=f"scaled ${order.target_notional_usd:.2f} → ${room:.2f} (market {market} cap)",
            scaled_size_usd=room,
            detail={"market": market, "open": current, "cap": cap_usd,
                    "original": order.target_notional_usd, "scaled": room},
        )


# ================================================================== #
# G6 GlobalExposure
# ================================================================== #
@dataclass(slots=True, frozen=True)
class GlobalExposureGuard:
    """G6: total open notional cap = capital × multiplier (implicit max
    leverage). Default 1.5× — modest leverage.

    No scaling: if you're at the leverage limit, taking a smaller piece
    of one strategy's signal isn't really risk-managing — better to
    pass on the trade until something closes. Hence DENY only.
    """

    name: str = "global_exposure"
    capital_multiplier: float = 1.5

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        cap_usd = ctx.capital_usd * self.capital_multiplier
        projected = ctx.global_open_notional + order.target_notional_usd
        if projected > cap_usd:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(f"global ${projected:.2f} > cap ${cap_usd:.2f} "
                        f"({self.capital_multiplier:.1f}× capital)"),
                detail={"current": ctx.global_open_notional, "request": order.target_notional_usd,
                        "cap": cap_usd, "multiplier": self.capital_multiplier},
            )
        return GuardDecision(self.name, GuardResult.ALLOW,
                             detail={"projected": projected, "cap": cap_usd})


# ================================================================== #
# G8 DailyLossCircuitBreaker
# ================================================================== #
@dataclass(slots=True, frozen=True)
class DailyLossCircuitBreakerGuard:
    """G8: deny all new orders when today's realised PnL is below
    `-loss_threshold_pct × capital`.

    Defaults to 5% (matches §15 D7 default). Use a `pnl_aggregator` to
    look up today's realised loss; the aggregator (PnLAggregator) is
    intentionally injected rather than baked in so tests can inject
    deterministic values.

    Note: only RESETS on UTC midnight (next day) because aggregator's
    `realised_today_usd` boundary rolls. Phase G v2 may add per-market
    timezone boundaries (e.g. NY 4pm close for US stocks).

    DENY only — once tripped, no scaling makes sense (the cap is "stop
    trading", not "trade smaller"). Daemon should also alert via Notifier
    when this triggers (round 21+).
    """

    name: str = "daily_loss_cb"
    loss_threshold_pct: float = 0.05         # 5% of capital
    pnl_aggregator: Any = None               # PnLAggregator (Protocol)

    def __post_init__(self):
        if self.pnl_aggregator is None:
            raise ValueError(
                "DailyLossCircuitBreakerGuard requires a pnl_aggregator"
            )

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        # Look up today's realised PnL (negative = loss). Failures from
        # the aggregator → fail open (allow), don't block all trades on
        # a flaky aggregator. Caller can wrap with strict aggregator if
        # they want fail-closed.
        try:
            pnl = float(self.pnl_aggregator.realised_today_usd())
        except Exception as e:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=f"pnl_aggregator failed: {type(e).__name__}: {e} — fail-open",
            )

        threshold_usd = -ctx.capital_usd * self.loss_threshold_pct
        if pnl <= threshold_usd:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"daily PnL ${pnl:.2f} ≤ threshold ${threshold_usd:.2f} "
                    f"({self.loss_threshold_pct:.0%} of ${ctx.capital_usd:.0f}) — "
                    f"circuit breaker open"
                ),
                detail={
                    "realised_pnl_today": pnl,
                    "threshold_usd": threshold_usd,
                    "threshold_pct": self.loss_threshold_pct,
                    "capital_usd": ctx.capital_usd,
                },
            )

        return GuardDecision(
            self.name, GuardResult.ALLOW,
            detail={"realised_pnl_today": pnl, "threshold_usd": threshold_usd},
        )


# ================================================================== #
# Helpers
# ================================================================== #
def _market_from_symbol(symbol: str) -> str:
    """Canonical symbols start with 'crypto:' / 'us:' / 'tw:' / 'fx:'.
    Defensive: returns the first colon-separated token (lowercase) or
    'unknown' if none."""
    if not symbol or ":" not in symbol:
        return "unknown"
    return symbol.split(":", 1)[0].lower()


__all__ = [
    "DailyLossCircuitBreakerGuard",
    "GlobalExposureGuard",
    "LatencyBudgetGuard",
    "MinSizeGuard",
    "PerMarketExposureGuard",
    "PerStrategyExposureGuard",
]
