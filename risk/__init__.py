"""L5 Risk layer — guard pipeline + position sizing.

Guards sit between the worker's `claim_next_pending` and the dispatcher's
`dispatch`. They can DENY (set status REJECTED with reason) or SCALE
(reduce target_notional) before the order reaches the exchange.

Pipeline order matters — short-circuits on first DENY. Scale guards
mutate order in-place; subsequent guards see the reduced size.

Rounds 18 + 20 ship 6 deterministic guards:
  G1 LatencyBudget       — stale signal → deny
  G3 MinSize             — dust order → deny
  G4 PerStrategyExposure — single-strategy notional cap → scale or deny
  G5 PerMarketExposure   — single-market cap → scale or deny
  G6 GlobalExposure      — total open notional cap (multiple of capital)
  G8 DailyLossCB         — today's realised loss > threshold → deny

Future rounds:
  G2 SymbolSupported     — needs F.1 exchange registry
  G7 CorrelationCap      — needs Phase G corr matrix worker
  G9 ConsecutiveLossCB   — needs day-rollup table
  G10 KellyPositionSize  — re-uses strategy DSL Kelly path; integrated here
                           once reflection.history seeds win_rate stats
"""

from risk.guards import (
    Guard,
    GuardContext,
    GuardDecision,
    GuardPipeline,
    GuardResult,
)
from risk.builtin_guards import (
    ConsecutiveLossDaysGuard,
    DailyLossCircuitBreakerGuard,
    GlobalExposureGuard,
    LatencyBudgetGuard,
    MinSizeGuard,
    PerMarketExposureGuard,
    PerStrategyExposureGuard,
)
from risk.pnl_aggregator import (
    InMemoryPnLAggregator,
    NoOpPnLAggregator,
    PnLAggregator,
    PostgresPnLAggregator,
    SupabasePnLAggregator,
    build_pnl_aggregator,
    day_boundary_utc,
)
from risk.exposure_provider import (
    ExposureProvider,
    InMemoryExposureProvider,
    NoOpExposureProvider,
    PostgresExposureProvider,
    SupabaseExposureProvider,
    build_exposure_provider,
    make_context_provider,
)

__all__ = [
    # framework
    "Guard",
    "GuardContext",
    "GuardDecision",
    "GuardPipeline",
    "GuardResult",
    # built-in guards
    "ConsecutiveLossDaysGuard",
    "DailyLossCircuitBreakerGuard",
    "GlobalExposureGuard",
    "LatencyBudgetGuard",
    "MinSizeGuard",
    "PerMarketExposureGuard",
    "PerStrategyExposureGuard",
    # pnl aggregator
    "PnLAggregator",
    "NoOpPnLAggregator",
    "InMemoryPnLAggregator",
    "SupabasePnLAggregator",
    "PostgresPnLAggregator",
    "build_pnl_aggregator",
    "day_boundary_utc",
    # exposure provider
    "ExposureProvider",
    "NoOpExposureProvider",
    "InMemoryExposureProvider",
    "SupabaseExposureProvider",
    "PostgresExposureProvider",
    "build_exposure_provider",
    "make_context_provider",
]
