"""L3 Fusion layer — regime detection + signal weighting + conflict resolution.

Components:
  - regime.py:  RegimeDetector (7 regimes + UNKNOWN, pure rules)
  - weights.py: load + validate the regime × source weight matrix YAML
  - fuser.py:   SignalFuser combines N UniversalSignals → 1 FusedSignal

Strategy DSL evaluator already consumes `regime` and `fused.*` from
context — wire the fuser into the daemon and the chain runs end-to-end.

Phase G adds reflection-driven calibration that proposes weight tweaks
based on per-source historical accuracy under each regime.
"""

from fusion.regime import (
    MarketContext,
    Regime,
    RegimeDetector,
    detect_regime,
)
from fusion.weights import (
    DEFAULT_WEIGHTS_PATH,
    WeightsError,
    get_weights_for,
    load_weights,
)
from fusion.fuser import (
    DEFAULT_CONFLICT_RATIO,
    FuserConfig,
    SignalFuser,
)

__all__ = [
    "MarketContext",
    "Regime",
    "RegimeDetector",
    "detect_regime",
    "DEFAULT_WEIGHTS_PATH",
    "WeightsError",
    "load_weights",
    "get_weights_for",
    "SignalFuser",
    "FuserConfig",
    "DEFAULT_CONFLICT_RATIO",
]
