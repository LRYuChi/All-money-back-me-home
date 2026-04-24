"""SignalFuser — combine N UniversalSignals into one FusedSignal.

Algorithm:
  1. Group input signals by direction (long / short / neutral).
  2. For each direction, score = Σ (weight[source] × signal.strength)
     using the regime's weight row. Sources with no weight are dropped.
  3. Pick winning direction = argmax(score).
  4. ensemble_score = winning_score / Σ(all weights used) — normalised
     to [0, 1] regardless of how many sources voted.
  5. Conflict detection: if runner-up score ≥ 2/3 of winner →
     mark conflict + halve ensemble_score (signals downstream that this
     ensemble is fragile).
  6. contributions dict captures per-source weighted contribution for
     audit / dashboard explainability.

Edge cases:
  - Empty signal list → FusedSignal(direction=NEUTRAL, ensemble_score=0)
  - All signals NEUTRAL → direction=NEUTRAL with the right strength
  - Stale signals (`is_expired`) included by default but down-weighted
    by `staleness_factor` (configurable).

The fuser is pure: same input + same weights → same output. Caller owns
clock; persistence to fused_signals table is L7's job.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

from fusion.regime import Regime
from fusion.weights import get_weights_for
from shared.signals.types import (
    Direction,
    FusedSignal,
    Horizon,
    UniversalSignal,
)

logger = logging.getLogger(__name__)


# Conflict threshold: runner-up score / winner score above this = conflict
DEFAULT_CONFLICT_RATIO = 2 / 3
# When marked conflict, multiply ensemble_score by this
CONFLICT_DOWNWEIGHT = 0.5
# Stale signals (past expires_at) get this weight multiplier
DEFAULT_STALENESS_FACTOR = 0.3


@dataclass(slots=True)
class FuserConfig:
    conflict_ratio: float = DEFAULT_CONFLICT_RATIO
    conflict_downweight: float = CONFLICT_DOWNWEIGHT
    staleness_factor: float = DEFAULT_STALENESS_FACTOR


class SignalFuser:
    """Stateless. Inject weights matrix once at construction; call fuse()
    per (symbol, horizon, regime) batch."""

    def __init__(
        self,
        weights: dict[str, dict[str, float]],
        *,
        config: FuserConfig | None = None,
    ) -> None:
        self._weights = weights
        self._cfg = config or FuserConfig()

    def fuse(
        self,
        signals: list[UniversalSignal],
        regime: Regime,
        *,
        symbol: str,
        horizon: Horizon,
        now: datetime | None = None,
    ) -> FusedSignal:
        """Combine signals into a FusedSignal. All signals MUST be for the
        same (symbol, horizon) — caller groups them upstream."""
        now = now or datetime.now(timezone.utc)
        weight_row = get_weights_for(regime, self._weights)

        if not signals:
            return _empty_fused(symbol, horizon, regime, ts=now)

        # Score each direction
        # contributions: per-source weighted contribution (post-staleness)
        scores: dict[Direction, float] = defaultdict(float)
        contributions: dict[str, float] = defaultdict(float)
        sources_seen: set[str] = set()
        total_weight_used = 0.0

        for sig in signals:
            source = sig.source.value
            if source not in weight_row:
                # Source not weighted in this regime → ignore (e.g.
                # `kronos: 0` or simply omitted)
                continue
            base_weight = weight_row[source]
            if base_weight <= 0:
                continue
            staleness = self._cfg.staleness_factor if _is_stale(sig, now) else 1.0
            effective = base_weight * staleness * sig.strength
            scores[sig.direction] += effective
            contributions[source] += effective
            sources_seen.add(source)
            total_weight_used += base_weight

        if not sources_seen:
            return _empty_fused(symbol, horizon, regime, ts=now)

        # Winning direction
        sorted_scores = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        winner_dir, winner_score = sorted_scores[0]
        runner_up = sorted_scores[1][1] if len(sorted_scores) > 1 else 0.0

        # Conflict detection
        conflict = winner_score > 0 and (runner_up / winner_score) >= self._cfg.conflict_ratio

        # Normalise ensemble_score to [0, 1]
        ensemble_score = winner_score / total_weight_used if total_weight_used else 0.0
        ensemble_score = min(1.0, max(0.0, ensemble_score))
        if conflict:
            ensemble_score *= self._cfg.conflict_downweight

        return FusedSignal(
            symbol=symbol,
            horizon=horizon,
            direction=winner_dir,
            ensemble_score=ensemble_score,
            regime=regime.value,
            sources_count=len(sources_seen),
            contributions=dict(contributions),
            conflict=conflict,
            ts=now,
        )


# ================================================================== #
# Helpers
# ================================================================== #
def _is_stale(sig: UniversalSignal, now: datetime) -> bool:
    return sig.expires_at is not None and now > sig.expires_at


def _empty_fused(symbol: str, horizon: str, regime: Regime, *, ts: datetime) -> FusedSignal:
    return FusedSignal(
        symbol=symbol,
        horizon=horizon,
        direction=Direction.NEUTRAL,
        ensemble_score=0.0,
        regime=regime.value,
        sources_count=0,
        contributions={},
        conflict=False,
        ts=ts,
    )


__all__ = ["SignalFuser", "FuserConfig", "DEFAULT_CONFLICT_RATIO"]
