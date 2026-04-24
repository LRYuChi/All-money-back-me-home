"""Reflection result types.

Pure data; no IO. Consumed by validator + by L7 reporter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Correctness(str, Enum):
    """Per-signal verdict after comparing to forward return.

    `direction == long`  → correct iff actual_return >  +threshold
    `direction == short` → correct iff actual_return <  -threshold
    `direction == neutral` → correct iff |actual_return| <= threshold

    `INCONCLUSIVE` covers the edge band where the move is too small to
    call (default ±0.2%). `MISSING_PRICE` distinguishes "we couldn't
    fetch a price" from "the signal was wrong" — important for accuracy
    metrics so missing data doesn't deflate scores.
    """

    CORRECT = "correct"
    INCORRECT = "incorrect"
    INCONCLUSIVE = "inconclusive"
    MISSING_PRICE = "missing_price"


@dataclass(slots=True, frozen=True)
class ValidationResult:
    """One signal's verdict. ID maps to signal_history.id."""

    signal_id: int
    correctness: Correctness
    actual_return_pct: float | None     # None when MISSING_PRICE
    entry_price: float | None
    exit_price: float | None
    notes: str = ""


@dataclass(slots=True)
class ValidationStats:
    """Aggregate of one validate_signals() invocation."""

    examined: int = 0
    correct: int = 0
    incorrect: int = 0
    inconclusive: int = 0
    missing_price: int = 0
    write_errors: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def validated(self) -> int:
        """How many signals received a definitive verdict (correct/incorrect/inconclusive)."""
        return self.correct + self.incorrect + self.inconclusive

    @property
    def hit_rate(self) -> float:
        """Among definitive verdicts, fraction correct."""
        decisive = self.correct + self.incorrect
        return self.correct / decisive if decisive else 0.0

    @property
    def coverage(self) -> float:
        """Fraction of examined that got a price (validated includes inconclusive)."""
        return self.validated / self.examined if self.examined else 0.0


__all__ = ["Correctness", "ValidationResult", "ValidationStats"]
