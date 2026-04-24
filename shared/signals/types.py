"""Universal Signal schema — common language across all signal sources.

Design rationale
----------------
Signal sources are heterogeneous:
  - Kronos: probabilistic forecast → we want p5/p50/p95 exposed
  - Smart Money: deterministic whale fills → we have wallet attribution
  - TA: rule-based crossovers → binary + strength
  - AI LLM: free-text reasoning → we want the narrative preserved
  - Macro: regime/sentiment → we need the sub-indicator attribution

Rather than force-fit one schema, we keep a small required core
(source/symbol/direction/strength/horizon) and let each source pack
its specifics into `details: dict`. This keeps fusion layer simple
(it only reads the core) while preserving audit-level detail.

No logic here — pure types. Persistence is in `history.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Literal


class SignalSource(str, Enum):
    """Who produced this signal. Closed set — new sources must extend here."""

    KRONOS = "kronos"
    SMART_MONEY = "smart_money"
    TA = "ta"
    AI_LLM = "ai_llm"
    MACRO = "macro"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"


# Standardized horizons — must match what strategies can consume.
# Keep short: if you need a new horizon, update regime weights + strategy
# DSL at the same time.
Horizon = Literal["15m", "1h", "4h", "1d"]
HORIZONS: tuple[str, ...] = ("15m", "1h", "4h", "1d")


def horizon_to_timedelta(h: str) -> timedelta:
    """Convert horizon string to timedelta for expiry calc."""
    return {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
    }[h]


@dataclass(slots=True, frozen=True)
class UniversalSignal:
    """One signal from one source about one symbol at one horizon.

    Contract:
        - `strength` is the source's internal confidence in [0, 1]. It is
          NOT comparable across sources — fusion layer applies source-wise
          weights from the regime matrix before combining.
        - `direction=neutral` means "source sees no opinion" (e.g. Kronos
          p5 and p95 straddle zero). It's not "flat the position"; the
          strategy layer interprets.
        - `expires_at` is advisory. The strategy layer may still act on an
          expired signal if no fresher one exists, but fusion will
          down-weight by staleness.
        - `details` is free-form per source. Schemas documented in each
          producer module (kronos_layer / smart_money / ...).
    """

    source: SignalSource
    symbol: str                      # canonical, e.g. "crypto:OKX:BTC/USDT:USDT"
    horizon: Horizon
    direction: Direction
    strength: float                  # [0, 1]
    reason: str                      # human-readable, 1-sentence
    details: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        # Validate first — before we try to derive expires_at from horizon,
        # which would raise KeyError on unknown horizons.
        if self.horizon not in HORIZONS:
            raise ValueError(f"horizon must be one of {HORIZONS}, got {self.horizon!r}")
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(f"strength must be in [0,1], got {self.strength}")
        # Dataclass with slots+frozen requires object.__setattr__ for defaults.
        if self.expires_at is None:
            object.__setattr__(
                self,
                "expires_at",
                self.ts + horizon_to_timedelta(self.horizon),
            )

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and datetime.now(timezone.utc) > self.expires_at

    @property
    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.ts).total_seconds()


@dataclass(slots=True, frozen=True)
class FusedSignal:
    """Fusion layer (L3) output — weighted combination of signals from N sources.

    Captures the decomposition so we can explain any decision: which sources
    contributed, their weights at this regime, and whether there was conflict.
    """

    symbol: str
    horizon: Horizon
    direction: Direction
    ensemble_score: float            # [0, 1] — aggregate confidence after weighting
    regime: str                      # from RegimeDetector
    sources_count: int               # how many distinct sources voted
    contributions: dict[str, float]  # source.value → weighted score contribution
    conflict: bool                   # true if runner-up direction > 2/3 of winner
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not 0.0 <= self.ensemble_score <= 1.0:
            raise ValueError(f"ensemble_score must be in [0,1], got {self.ensemble_score}")


@dataclass(slots=True, frozen=True)
class StrategyIntent:
    """Strategy layer (L4) output — concrete trading intent, pre-sizing.

    L5 (risk) turns this into a SizedOrder after guards.
    """

    strategy_id: str
    symbol: str
    direction: Direction
    target_notional_usd: float       # suggested exposure; may be scaled by L5
    entry_price_ref: float | None    # for limit orders; None = market
    stop_loss_pct: float | None
    take_profit_pct: float | None
    source_fused: FusedSignal        # audit trail
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


__all__ = [
    "SignalSource",
    "Direction",
    "Horizon",
    "HORIZONS",
    "horizon_to_timedelta",
    "UniversalSignal",
    "FusedSignal",
    "StrategyIntent",
]
