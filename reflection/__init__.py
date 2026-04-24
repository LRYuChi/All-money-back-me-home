"""L7 Reflection — close the feedback loop.

Reads `signal_history` rows that are at least one horizon old, compares
each signal's `direction` against the actual forward return on its
symbol, and writes `was_correct` + `actual_return_pct` back.

Downstream consumers:
- `reflection/calibration.py` (Phase D): re-tune `fusion_weights.yaml`
- `reflection/reporter.py` (Phase D): weekly Telegram report
- `apps/api/src/routers/smart_money.py` (Phase D): per-source accuracy
  on the dashboard

Not yet wired:
- Real `HLPriceFetcher` — currently MockPriceFetcher only (Phase C)
- Cron schedule — once HL fetcher lands, runs hourly
"""

from reflection.types import ValidationStats, ValidationResult, Correctness
from reflection.validator import validate_signals
from reflection.price import (
    PriceFetcher,
    InMemoryPriceFetcher,
    PriceUnavailable,
)

__all__ = [
    "ValidationStats",
    "ValidationResult",
    "Correctness",
    "validate_signals",
    "PriceFetcher",
    "InMemoryPriceFetcher",
    "PriceUnavailable",
]
