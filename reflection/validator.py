"""Reflection validator — turn raw signal_history rows into verdicts.

Pure-function design:
  validate_signals(reader, updater, prices, ...)

The orchestrator pulls unvalidated rows that are at least one horizon
old (so the forward window has elapsed), looks up the entry+exit price,
classifies into Correctness, and pushes verdicts back via the updater.

No async, no DB clients here — those live behind Protocols so this
module is fully unit-testable on InMemory implementations.

Performance notes (Phase B - C):
  - Single-threaded, sequential. At ~100-1000 signals/day this is fine.
  - When signal volume reaches the 10k/day mark (Phase D after Kronos +
    AI + 3 markets), parallelise via ThreadPoolExecutor on per-row
    fetch — DB updates can stay sequential since they're cheap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol

from reflection.price import PriceFetcher, PriceUnavailable
from reflection.types import Correctness, ValidationResult, ValidationStats
from shared.signals.types import HORIZONS, horizon_to_timedelta

logger = logging.getLogger(__name__)


# Default minimum return that counts as a directional move.
# Below this band, signals are INCONCLUSIVE rather than wrong.
DEFAULT_CORRECTNESS_THRESHOLD: float = 0.002   # 0.2%


@dataclass(slots=True, frozen=True)
class UnvalidatedRow:
    """A row from signal_history awaiting verdict.

    Mirrors the migration 016 schema columns we need; reader fills these.
    """

    id: int
    symbol: str
    horizon: str
    direction: str           # 'long' | 'short' | 'neutral'
    ts: datetime
    expires_at: datetime


class SignalHistoryReader(Protocol):
    """Yields rows from signal_history that need validation.

    Implementations:
      - SupabaseReader: SELECT * FROM signal_history WHERE validated_at IS NULL
        AND ts < now() - INTERVAL '<horizon>' LIMIT N
      - InMemoryReader (tests): pre-loaded list
    """

    def read_unvalidated(
        self, *, max_age_hours: int, limit: int
    ) -> Iterable[UnvalidatedRow]: ...


class SignalHistoryUpdater(Protocol):
    """Writes verdict back to signal_history."""

    def update_verdict(
        self,
        signal_id: int,
        *,
        was_correct: bool | None,
        actual_return_pct: float | None,
        validated_at: datetime,
    ) -> None: ...


# ================================================================== #
# Core
# ================================================================== #
def validate_signals(
    reader: SignalHistoryReader,
    updater: SignalHistoryUpdater,
    prices: PriceFetcher,
    *,
    max_age_hours: int = 24 * 14,
    limit: int = 200,
    correctness_threshold: float = DEFAULT_CORRECTNESS_THRESHOLD,
    now: datetime | None = None,
) -> ValidationStats:
    """Validate one batch of unvalidated signals.

    Args:
        reader: yields UnvalidatedRow.
        updater: receives verdicts.
        prices: PriceFetcher implementation.
        max_age_hours: only consider signals at most this old (don't try
            to validate ancient signals where price is unavailable anyway).
        limit: cap per invocation. Cron typically runs hourly.
        correctness_threshold: |return| <= this → INCONCLUSIVE. Match
            the strategy layer's noise floor; default 0.2% is sensible
            for crypto 15m-4h horizons.
        now: timestamp for "current time" in tests; defaults to UTC now.
    """
    now = now or datetime.now(timezone.utc)
    stats = ValidationStats(started_at=now)

    for row in reader.read_unvalidated(max_age_hours=max_age_hours, limit=limit):
        stats.examined += 1
        result = _classify_one(row, prices, now=now, threshold=correctness_threshold)
        try:
            _apply_verdict(updater, result, validated_at=now)
        except Exception as e:
            stats.write_errors += 1
            logger.warning(
                "validator: failed to update signal_id=%d: %s",
                row.id, e,
            )
            continue

        if result.correctness == Correctness.CORRECT:
            stats.correct += 1
        elif result.correctness == Correctness.INCORRECT:
            stats.incorrect += 1
        elif result.correctness == Correctness.INCONCLUSIVE:
            stats.inconclusive += 1
        else:
            stats.missing_price += 1

    stats.finished_at = datetime.now(timezone.utc)
    logger.info(
        "validator: examined=%d correct=%d incorrect=%d inconclusive=%d "
        "missing=%d errors=%d hit_rate=%.2f",
        stats.examined, stats.correct, stats.incorrect,
        stats.inconclusive, stats.missing_price, stats.write_errors,
        stats.hit_rate,
    )
    return stats


# ================================================================== #
# Helpers
# ================================================================== #
def _classify_one(
    row: UnvalidatedRow,
    prices: PriceFetcher,
    *,
    now: datetime,
    threshold: float,
) -> ValidationResult:
    """Compute verdict for one row. Pure (modulo PriceFetcher)."""
    if row.horizon not in HORIZONS:
        return ValidationResult(
            signal_id=row.id, correctness=Correctness.MISSING_PRICE,
            actual_return_pct=None, entry_price=None, exit_price=None,
            notes=f"unknown horizon {row.horizon!r}",
        )

    forward_ts = row.ts + horizon_to_timedelta(row.horizon)
    if forward_ts > now:
        # Should not happen if reader respected the age filter, but defensive
        return ValidationResult(
            signal_id=row.id, correctness=Correctness.MISSING_PRICE,
            actual_return_pct=None, entry_price=None, exit_price=None,
            notes="forward window not elapsed",
        )

    try:
        entry = prices.get_close_at(row.symbol, row.ts)
        exit_ = prices.get_close_at(row.symbol, forward_ts)
    except PriceUnavailable as e:
        return ValidationResult(
            signal_id=row.id, correctness=Correctness.MISSING_PRICE,
            actual_return_pct=None, entry_price=None, exit_price=None,
            notes=str(e),
        )

    if entry == 0:
        return ValidationResult(
            signal_id=row.id, correctness=Correctness.MISSING_PRICE,
            actual_return_pct=None, entry_price=entry, exit_price=exit_,
            notes="entry price is zero — division undefined",
        )

    actual_return = (exit_ - entry) / entry
    correctness = _verdict(row.direction, actual_return, threshold)
    return ValidationResult(
        signal_id=row.id,
        correctness=correctness,
        actual_return_pct=actual_return,
        entry_price=entry,
        exit_price=exit_,
    )


def _verdict(
    direction: str, actual_return: float, threshold: float
) -> Correctness:
    """Apply directional rules to determine Correctness."""
    if direction == "long":
        if actual_return > threshold:
            return Correctness.CORRECT
        if actual_return < -threshold:
            return Correctness.INCORRECT
        return Correctness.INCONCLUSIVE
    if direction == "short":
        if actual_return < -threshold:
            return Correctness.CORRECT
        if actual_return > threshold:
            return Correctness.INCORRECT
        return Correctness.INCONCLUSIVE
    if direction == "neutral":
        if abs(actual_return) <= threshold:
            return Correctness.CORRECT
        return Correctness.INCORRECT
    # Unknown direction value — defensive
    return Correctness.MISSING_PRICE


def _apply_verdict(
    updater: SignalHistoryUpdater,
    result: ValidationResult,
    *,
    validated_at: datetime,
) -> None:
    """Translate Correctness → was_correct boolean and write back.

    INCONCLUSIVE → was_correct=None (signals "we tried but couldn't tell")
    so dashboards can distinguish "no opinion" from "wrong".
    """
    was_correct: bool | None
    if result.correctness == Correctness.CORRECT:
        was_correct = True
    elif result.correctness == Correctness.INCORRECT:
        was_correct = False
    elif result.correctness == Correctness.INCONCLUSIVE:
        was_correct = None
    else:  # MISSING_PRICE: don't even mark validated_at, so we retry next round
        return
    updater.update_verdict(
        result.signal_id,
        was_correct=was_correct,
        actual_return_pct=result.actual_return_pct,
        validated_at=validated_at,
    )


__all__ = [
    "DEFAULT_CORRECTNESS_THRESHOLD",
    "UnvalidatedRow",
    "SignalHistoryReader",
    "SignalHistoryUpdater",
    "validate_signals",
]
