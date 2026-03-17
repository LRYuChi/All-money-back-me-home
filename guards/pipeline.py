"""Default guard pipeline factory."""

from guards.base import GuardPipeline
from guards.guards import (
    ConsecutiveLossGuard,
    CooldownGuard,
    DailyLossGuard,
    MaxLeverageGuard,
    MaxPositionGuard,
)


def create_default_pipeline() -> GuardPipeline:
    """Create the default guard pipeline with conservative settings."""
    pipeline = GuardPipeline([
        MaxPositionGuard(max_pct=30),
        MaxLeverageGuard(max_leverage=5),
        CooldownGuard(minutes=15),
        DailyLossGuard(max_pct=5),
        ConsecutiveLossGuard(max_streak=5, pause_hours=24),
    ])
    return pipeline
