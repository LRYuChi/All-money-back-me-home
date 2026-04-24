"""L3 Fusion layer — regime detection + signal weighting + conflict resolution.

This round (11): regime detector. Future:
  - fuser.py: weighted ensemble of N source signals using per-regime
    weight matrix
  - weights.yaml: regime × source weight config (initial human-tuned,
    Phase G adds reflection-driven calibration)
  - conflict_resolver.py: when signals disagree sharply, decide policy

Strategy DSL evaluator already consumes `regime` from context, so once
this module is wired into the daemon (Phase D round 2+), strategies can
gate entries on regime (e.g. `none_of: regime == "CRISIS"`).
"""

from fusion.regime import (
    MarketContext,
    Regime,
    RegimeDetector,
    detect_regime,
)

__all__ = [
    "MarketContext",
    "Regime",
    "RegimeDetector",
    "detect_regime",
]
