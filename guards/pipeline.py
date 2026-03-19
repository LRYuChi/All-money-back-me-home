"""Default guard pipeline factory — singleton to preserve state across calls.

The pipeline MUST be a singleton so that stateful guards (CooldownGuard,
DailyLossGuard, ConsecutiveLossGuard) retain their tracking data between
trade checks. Creating a new pipeline on every call would reset all state.
"""

from guards.base import GuardPipeline
from guards.guards import (
    ConsecutiveLossGuard,
    CooldownGuard,
    DailyLossGuard,
    MaxLeverageGuard,
    MaxPositionGuard,
    TotalExposureGuard,
)

# Module-level singleton — persists for the lifetime of the process
_default_pipeline: GuardPipeline | None = None


def create_default_pipeline() -> GuardPipeline:
    """Return the singleton guard pipeline (creates on first call)."""
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = GuardPipeline([
            MaxPositionGuard(max_pct=30),
            MaxLeverageGuard(max_leverage=5),
            TotalExposureGuard(max_pct=80),
            CooldownGuard(minutes=15),
            DailyLossGuard(max_pct=5),
            ConsecutiveLossGuard(max_streak=5, pause_hours=24),
        ])
    return _default_pipeline


def get_guard(guard_type: type):
    """Get a specific guard instance from the singleton pipeline."""
    pipeline = create_default_pipeline()
    for g in pipeline.guards:
        if isinstance(g, guard_type):
            return g
    return None
