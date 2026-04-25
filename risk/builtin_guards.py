"""Built-in guards (G1, G3, G4, G5, G6 — round 18; G8 — round 20;
G9 — round 22; G7 — round 29)."""
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
# G10 KellyPositionSize (round 30)
# ================================================================== #
@dataclass(slots=True, frozen=True)
class KellyPositionGuard:
    """G10: cap order size at fractional Kelly recommendation.

    Looks up `WinRateStats` for (strategy_id, symbol) over `lookback_days`,
    computes Kelly fraction, applies `safety_factor` (default 0.25 =
    quarter-Kelly), and caps the order at `capital_usd × kelly × factor`.

    Decisions:
      - Insufficient sample (n_trades < min_trades) → ALLOW (we don't trust
        sparse stats; PerStrategy/PerMarket caps still apply)
      - Provider returns None / raises → ALLOW (fail-open; matches G1/G8/G9)
      - Negative-edge (kelly < 0) → DENY (don't trade a losing strategy)
      - Order size ≤ kelly cap → ALLOW unchanged
      - Order size > kelly cap → SCALE to cap (or DENY if scaled below floor)

    `safety_factor` notes:
      - 0.25 (default) = quarter-Kelly, conservative; tolerates the high
        estimation noise typical of small samples
      - 0.50 = half-Kelly, more aggressive
      - 1.00 = full Kelly, only for very stable, well-calibrated edges
    """

    name: str = "kelly_size"
    win_rate_provider: Any = None        # WinRateProvider (Protocol)
    safety_factor: float = 0.25
    min_trades: int = 30
    lookback_days: int = 30
    deny_floor_pct: float = 0.10
    by_symbol: bool = False              # True → key on symbol; default keys
                                         # on strategy_id

    def __post_init__(self):
        if self.win_rate_provider is None:
            raise ValueError("KellyPositionGuard requires a win_rate_provider")
        if not (0.0 < self.safety_factor <= 1.0):
            raise ValueError(
                f"safety_factor must be in (0,1]; got {self.safety_factor}"
            )
        if self.min_trades < 1:
            raise ValueError(
                f"min_trades must be ≥ 1; got {self.min_trades}"
            )

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        try:
            stats = self.win_rate_provider.stats(
                strategy_id=order.strategy_id if not self.by_symbol else None,
                symbol=order.symbol if self.by_symbol else None,
                lookback_days=self.lookback_days,
            )
        except Exception as e:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=f"win_rate_provider failed: {type(e).__name__}: {e} — fail-open",
            )

        if stats is None:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason="no win_rate stats available — fail-open",
            )

        if stats.n_trades < self.min_trades:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=(
                    f"insufficient sample ({stats.n_trades} < "
                    f"{self.min_trades} trades) — fail-open"
                ),
                detail={"n_trades": stats.n_trades, "min_trades": self.min_trades},
            )

        kelly = stats.kelly_fraction
        if kelly <= 0:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"negative-edge stats (Kelly={kelly:.3f}, "
                    f"win_rate={stats.win_rate:.2%}, "
                    f"avg_win={stats.avg_win_pct:.2%}, "
                    f"avg_loss={stats.avg_loss_pct:.2%}) — strategy not "
                    f"profitable, do not size"
                ),
                detail={
                    "kelly_fraction": kelly,
                    "win_rate": stats.win_rate,
                    "avg_win_pct": stats.avg_win_pct,
                    "avg_loss_pct": stats.avg_loss_pct,
                    "n_trades": stats.n_trades,
                },
            )

        cap_usd = ctx.capital_usd * kelly * self.safety_factor

        if order.target_notional_usd <= cap_usd:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                detail={
                    "kelly_fraction": kelly,
                    "safety_factor": self.safety_factor,
                    "kelly_cap_usd": cap_usd,
                    "request": order.target_notional_usd,
                },
            )

        # Scale down to Kelly cap, with floor protection
        floor = order.target_notional_usd * self.deny_floor_pct
        if cap_usd < floor:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"Kelly cap ${cap_usd:.2f} below {self.deny_floor_pct:.0%} "
                    f"of request ${order.target_notional_usd:.2f}"
                ),
                detail={
                    "kelly_fraction": kelly,
                    "kelly_cap_usd": cap_usd,
                    "request": order.target_notional_usd,
                    "floor": floor,
                },
            )
        return GuardDecision(
            self.name, GuardResult.SCALE,
            reason=(
                f"scaled ${order.target_notional_usd:.2f} → ${cap_usd:.2f} "
                f"(Kelly fraction {kelly:.3f} × safety {self.safety_factor:.2f}, "
                f"n={stats.n_trades})"
            ),
            scaled_size_usd=cap_usd,
            detail={
                "kelly_fraction": kelly,
                "safety_factor": self.safety_factor,
                "kelly_cap_usd": cap_usd,
                "original": order.target_notional_usd,
                "scaled": cap_usd,
                "n_trades": stats.n_trades,
            },
        )


# ================================================================== #
# G7 CorrelationCap (round 29)
# ================================================================== #
@dataclass(slots=True, frozen=True)
class CorrelationCapGuard:
    """G7: cap aggregate notional within a "correlated cluster".

    For a new order on symbol X:
      1. Find every currently-open symbol Y with |ρ(X,Y)| ≥ correlation_threshold
      2. Sum their open notionals = cluster_open
      3. Projected = cluster_open + new_order.target_notional_usd
      4. cap = capital_usd × cluster_cap_pct
      5. If projected > cap: scale down to fit (or DENY if room is below
         deny_floor_pct of the original request)

    `matrix` (CorrelationMatrix) supplies pairwise ρ. NoOp matrix → every
    pair returns 0 → G7 never trips (fail-open by default — matrix is
    opt-in via `--correlation-cap` / matrix-path config).

    Self-correlation note: the new symbol vs itself is always counted —
    if you already have BTC at $4k and request more BTC, that's the same
    cluster. Matrix's default_self handles this (1.0).

    Idempotent vs G4/G5: G7 catches a different failure mode (you might
    have BTC under per-strategy cap and crypto under per-market cap, but
    BTC + ETH + SOL together under correlation cluster cap is a separate
    constraint).
    """

    name: str = "correlation_cap"
    matrix: Any = None                     # CorrelationMatrix (Protocol)
    correlation_threshold: float = 0.70    # |ρ| ≥ this counts as "correlated"
    cluster_cap_pct: float = 0.40          # cluster ≤ 40% of capital
    deny_floor_pct: float = 0.10           # if scaled < 10% of request → DENY

    def __post_init__(self):
        if self.matrix is None:
            raise ValueError(
                "CorrelationCapGuard requires a `matrix` (CorrelationMatrix)"
            )
        if not (0.0 <= self.correlation_threshold <= 1.0):
            raise ValueError(
                f"correlation_threshold must be in [0,1]; got "
                f"{self.correlation_threshold}"
            )

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        # Find all currently-open symbols that correlate strongly with the
        # new order's symbol. Self always counts (existing position in
        # the same symbol is part of the same cluster).
        try:
            cluster_symbols: list[tuple[str, float, float]] = []  # (sym, notional, rho)
            cluster_open = 0.0
            for sym, open_usd in ctx.open_notional_by_symbol.items():
                if open_usd <= 0:
                    continue
                rho = float(self.matrix.get(order.symbol, sym))
                if abs(rho) >= self.correlation_threshold:
                    cluster_symbols.append((sym, open_usd, rho))
                    cluster_open += open_usd
        except Exception as e:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=f"matrix lookup failed: {type(e).__name__}: {e} — fail-open",
            )

        cap_usd = ctx.capital_usd * self.cluster_cap_pct
        room = cap_usd - cluster_open

        if room <= 0:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"correlation cluster around {order.symbol} at cap "
                    f"(open ${cluster_open:.2f} ≥ cap ${cap_usd:.2f}, "
                    f"threshold |ρ|≥{self.correlation_threshold:.2f}, "
                    f"{len(cluster_symbols)} correlated symbols)"
                ),
                detail={
                    "cluster_symbols": [s for s, _, _ in cluster_symbols],
                    "cluster_open": cluster_open,
                    "cap": cap_usd,
                    "request": order.target_notional_usd,
                    "threshold": self.correlation_threshold,
                },
            )

        if order.target_notional_usd <= room:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                detail={
                    "cluster_symbols": [s for s, _, _ in cluster_symbols],
                    "cluster_open": cluster_open,
                    "cap": cap_usd,
                    "request": order.target_notional_usd,
                },
            )

        # Need to scale
        scaled = room
        floor = order.target_notional_usd * self.deny_floor_pct
        if scaled < floor:
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"correlation cluster room ${room:.2f} below "
                    f"{self.deny_floor_pct:.0%} of request "
                    f"${order.target_notional_usd:.2f}"
                ),
                detail={
                    "cluster_symbols": [s for s, _, _ in cluster_symbols],
                    "cluster_open": cluster_open,
                    "cap": cap_usd,
                    "scaled": scaled,
                    "floor": floor,
                },
            )
        return GuardDecision(
            self.name, GuardResult.SCALE,
            reason=(
                f"scaled ${order.target_notional_usd:.2f} → ${scaled:.2f} "
                f"(correlation cluster around {order.symbol}, "
                f"{len(cluster_symbols)} symbols)"
            ),
            scaled_size_usd=scaled,
            detail={
                "cluster_symbols": [s for s, _, _ in cluster_symbols],
                "cluster_open": cluster_open,
                "cap": cap_usd,
                "original": order.target_notional_usd,
                "scaled": scaled,
            },
        )


# ================================================================== #
# G9 ConsecutiveLossDays
# ================================================================== #
@dataclass(slots=True, frozen=True)
class ConsecutiveLossDaysGuard:
    """G9: deny when N consecutive UTC days have realised PnL < 0.

    Defaults to 3 (matches §15 D7 'consecutive 3 days → manual unlock').
    Once tripped, requires human reset — but this guard alone doesn't
    persist that decision; daemon should also alert via Notifier and
    flip the strategy registry to disabled (human re-enables) for now.

    Insufficient history (< N completed days available) → ALLOW (we can't
    tell if losses are genuinely consecutive). G9 only fires once we
    have a clear N-day streak.

    Aggregator failures → fail open (same rationale as G8).
    """

    name: str = "consecutive_loss_cb"
    max_consecutive_losses: int = 3
    pnl_aggregator: Any = None

    def __post_init__(self):
        if self.pnl_aggregator is None:
            raise ValueError(
                "ConsecutiveLossDaysGuard requires a pnl_aggregator"
            )
        if self.max_consecutive_losses < 1:
            raise ValueError(
                f"max_consecutive_losses must be >= 1, got {self.max_consecutive_losses}"
            )

    def check(self, order: PendingOrder, ctx: GuardContext) -> GuardDecision:
        try:
            history = list(self.pnl_aggregator.daily_pnl_history(
                days=self.max_consecutive_losses,
            ))
        except Exception as e:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=f"pnl_aggregator failed: {type(e).__name__}: {e} — fail-open",
            )

        # Insufficient data — can't yet detect a streak
        if len(history) < self.max_consecutive_losses:
            return GuardDecision(
                self.name, GuardResult.ALLOW,
                reason=f"only {len(history)}/{self.max_consecutive_losses} days available",
                detail={"history": history,
                        "required_days": self.max_consecutive_losses},
            )

        # All N most-recent completed days negative → trip
        recent = history[-self.max_consecutive_losses:]
        if all(p < 0 for p in recent):
            return GuardDecision(
                self.name, GuardResult.DENY,
                reason=(
                    f"{self.max_consecutive_losses} consecutive losing days "
                    f"({recent}) — human review required"
                ),
                detail={
                    "history": history,
                    "recent_losses": recent,
                    "max_consecutive_losses": self.max_consecutive_losses,
                },
            )

        return GuardDecision(
            self.name, GuardResult.ALLOW,
            detail={"history": history, "recent_losses_streak": _trailing_loss_streak(history)},
        )


def _trailing_loss_streak(history: list[float]) -> int:
    """Count negative values from the right end of the list, stopping at
    first non-negative."""
    streak = 0
    for p in reversed(history):
        if p < 0:
            streak += 1
        else:
            break
    return streak


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
    "ConsecutiveLossDaysGuard",
    "CorrelationCapGuard",
    "DailyLossCircuitBreakerGuard",
    "GlobalExposureGuard",
    "KellyPositionGuard",
    "LatencyBudgetGuard",
    "MinSizeGuard",
    "PerMarketExposureGuard",
    "PerStrategyExposureGuard",
]
