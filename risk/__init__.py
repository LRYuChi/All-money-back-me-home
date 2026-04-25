"""L5 Risk layer — guard pipeline + position sizing.

Guards sit between the worker's `claim_next_pending` and the dispatcher's
`dispatch`. They can DENY (set status REJECTED with reason) or SCALE
(reduce target_notional) before the order reaches the exchange.

Pipeline order matters — short-circuits on first DENY. Scale guards
mutate order in-place; subsequent guards see the reduced size.

Rounds 18 + 20 + 22 + 23 ship 7 deterministic guards:
  G1 LatencyBudget       — stale signal → deny (now real via SignalAgeProvider)
  G3 MinSize             — dust order → deny
  G4 PerStrategyExposure — single-strategy notional cap → scale or deny
  G5 PerMarketExposure   — single-market cap → scale or deny
  G6 GlobalExposure      — total open notional cap (multiple of capital)
  G8 DailyLossCB         — today's realised loss > threshold → deny
  G9 ConsecutiveLossCB   — N consecutive losing UTC days → deny

Future rounds:
  G2 SymbolSupported     — needs F.1 exchange registry
  G7 CorrelationCap      — needs Phase G corr matrix worker
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
    CorrelationCapGuard,
    DailyLossCircuitBreakerGuard,
    GlobalExposureGuard,
    KellyPositionGuard,
    LatencyBudgetGuard,
    MinSizeGuard,
    PerMarketExposureGuard,
    PerStrategyExposureGuard,
)
from risk.correlation_matrix import (
    CorrelationMatrix,
    InMemoryCorrelationMatrix,
    NoOpCorrelationMatrix,
    YamlCorrelationMatrix,
    build_correlation_matrix,
)
from risk.win_rate_provider import (
    InMemoryWinRateProvider,
    NoOpWinRateProvider,
    PostgresWinRateProvider,
    WinRateProvider,
    WinRateStats,
    build_win_rate_provider,
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
from risk.signal_age_provider import (
    InMemorySignalAgeProvider,
    NoOpSignalAgeProvider,
    PostgresSignalAgeProvider,
    SignalAgeProvider,
    SupabaseSignalAgeProvider,
    build_signal_age_provider,
)
from risk.side_effects import (
    GuardSideEffectHandler,
    chain_handlers,
    make_g9_strategy_disabler,
    make_guard_notifier_handler,
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
    "CorrelationCapGuard",
    "DailyLossCircuitBreakerGuard",
    "GlobalExposureGuard",
    "KellyPositionGuard",
    "LatencyBudgetGuard",
    "MinSizeGuard",
    "PerMarketExposureGuard",
    "PerStrategyExposureGuard",
    # correlation matrix (G7 input)
    "CorrelationMatrix",
    "NoOpCorrelationMatrix",
    "InMemoryCorrelationMatrix",
    "YamlCorrelationMatrix",
    "build_correlation_matrix",
    # win-rate provider (G10 input)
    "WinRateProvider",
    "WinRateStats",
    "NoOpWinRateProvider",
    "InMemoryWinRateProvider",
    "PostgresWinRateProvider",
    "build_win_rate_provider",
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
    # signal age provider (G1)
    "SignalAgeProvider",
    "NoOpSignalAgeProvider",
    "InMemorySignalAgeProvider",
    "SupabaseSignalAgeProvider",
    "PostgresSignalAgeProvider",
    "build_signal_age_provider",
    # side effects (G9 auto-disable etc.)
    "GuardSideEffectHandler",
    "chain_handlers",
    "make_g9_strategy_disabler",
    "make_guard_notifier_handler",
]
