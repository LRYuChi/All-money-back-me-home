"""IntentDeduper — prevents accidental double-enqueue of near-identical intents.

The risk closed here:
  - Strategy fires intent at t=100ms (coid = hash(..., 100, ...))
  - Strategy fires intent again at t=200ms (different coid)
  - PostgreSQL UNIQUE on client_order_id doesn't catch these (different coids)
  - Both enqueue, both dispatch, both fill → double position

A dedup window check at the intent layer (BEFORE intent_to_pending) skips
the second intent when (strategy_id, symbol, side) was seen within the
configured window. Two backends:
  - NoOp                   — disable dedup (default backwards compat)
  - WindowedIntentDeduper  — in-memory; per-process daemon
  - QueueBasedIntentDeduper — DB-backed; scaled-out workers + restarts

Window choice:
  - Strategy ticks at 30s → window 60s catches duplicate ticks
  - Strategy ticks at 5m  → window 300s
  - Set to 0 to disable (NoOpIntentDeduper).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from shared.signals.types import Direction, StrategyIntent

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class DedupKey:
    """The (strategy, symbol, side) tuple that defines "the same trade"."""
    strategy_id: str
    symbol: str
    side: str   # "long" or "short" (ccxt-aligned)

    @classmethod
    def from_intent(cls, intent: StrategyIntent) -> "DedupKey":
        side = "long" if intent.direction == Direction.LONG else "short"
        return cls(intent.strategy_id, intent.symbol, side)


class IntentDeduper(Protocol):
    def is_duplicate(self, intent: StrategyIntent) -> bool: ...
    def record(self, intent: StrategyIntent) -> None: ...


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpIntentDeduper:
    """Always allows. Use when dedup is intentionally disabled."""

    def is_duplicate(self, intent: StrategyIntent) -> bool:
        return False

    def record(self, intent: StrategyIntent) -> None:
        return


# ================================================================== #
# WindowedIntentDeduper — in-memory, thread-safe
# ================================================================== #
class WindowedIntentDeduper:
    """Tracks last seen intent per DedupKey; rejects re-arrivals within
    `window_sec`. Thread-safe via internal lock.

    Memory-bounded by `max_keys` (LRU-style eviction of oldest by
    last_seen). Default 10k keys ≈ 10k strategies × symbols.
    """

    def __init__(self, *, window_sec: float, max_keys: int = 10_000):
        if window_sec < 0:
            raise ValueError(f"window_sec must be ≥ 0; got {window_sec}")
        if max_keys < 1:
            raise ValueError(f"max_keys must be ≥ 1; got {max_keys}")
        self._window = timedelta(seconds=window_sec)
        self._max_keys = max_keys
        self._last_seen: dict[DedupKey, datetime] = {}
        self._lock = threading.Lock()

    def is_duplicate(self, intent: StrategyIntent) -> bool:
        if self._window.total_seconds() == 0:
            return False
        key = DedupKey.from_intent(intent)
        with self._lock:
            last = self._last_seen.get(key)
        if last is None:
            return False
        # Use intent.ts so test injection is deterministic; fall back to
        # now() if intent has no usable ts (defensive).
        ts = _coerce_aware_utc(intent.ts) or datetime.now(timezone.utc)
        return (ts - last) < self._window

    def record(self, intent: StrategyIntent) -> None:
        key = DedupKey.from_intent(intent)
        ts = _coerce_aware_utc(intent.ts) or datetime.now(timezone.utc)
        with self._lock:
            self._last_seen[key] = ts
            self._evict_if_needed_locked()

    def _evict_if_needed_locked(self) -> None:
        """LRU-evict oldest entries when cache exceeds max_keys."""
        if len(self._last_seen) <= self._max_keys:
            return
        # Drop ~10% of oldest entries to amortise eviction cost
        to_drop = max(1, len(self._last_seen) - self._max_keys + (self._max_keys // 10))
        sorted_keys = sorted(self._last_seen.items(), key=lambda kv: kv[1])
        for k, _ in sorted_keys[:to_drop]:
            del self._last_seen[k]

    # Introspection
    def size(self) -> int:
        with self._lock:
            return len(self._last_seen)


# ================================================================== #
# QueueBasedIntentDeduper — DB-backed (round 44+)
# ================================================================== #
class QueueBasedIntentDeduper:
    """Looks up recent pending_orders rows for (strategy, symbol, side)
    in the dedup window. Survives daemon restarts (state lives in DB).

    Slower than WindowedIntentDeduper (one DB query per is_duplicate)
    but the right choice when:
      - Multiple worker processes share the queue
      - Daemon restarts shouldn't reset dedup state
      - Compliance requires a DB-resident audit trail of skips
    """

    def __init__(
        self,
        queue: Any,        # PendingOrderQueue
        *,
        window_sec: float,
        scan_limit: int = 100,
    ):
        if window_sec < 0:
            raise ValueError(f"window_sec must be ≥ 0; got {window_sec}")
        self._queue = queue
        self._window = timedelta(seconds=window_sec)
        self._scan_limit = scan_limit

    def is_duplicate(self, intent: StrategyIntent) -> bool:
        if self._window.total_seconds() == 0:
            return False
        key = DedupKey.from_intent(intent)
        ts = _coerce_aware_utc(intent.ts) or datetime.now(timezone.utc)
        cutoff = ts - self._window

        try:
            recent = self._queue.list_recent(limit=self._scan_limit)
        except Exception as e:
            logger.warning(
                "QueueBasedIntentDeduper: list_recent failed (%s) — "
                "fail-open (treat as not-duplicate)", e,
            )
            return False

        for row in recent:
            if row.strategy_id != key.strategy_id:
                continue
            if row.symbol != key.symbol:
                continue
            if row.side != key.side:
                continue
            row_ts = _coerce_aware_utc(row.created_at)
            if row_ts is None:
                continue
            if row_ts >= cutoff:
                return True
        return False

    def record(self, intent: StrategyIntent) -> None:
        # No-op: the act of enqueuing the order populates the queue,
        # which is the dedup source-of-truth. Caller's intent_to_pending +
        # queue.enqueue does the recording.
        return


def _coerce_aware_utc(ts) -> datetime | None:
    """Return a UTC-aware datetime or None. Tolerant of None/naive input."""
    if ts is None:
        return None
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


__all__ = [
    "DedupKey",
    "IntentDeduper",
    "NoOpIntentDeduper",
    "WindowedIntentDeduper",
    "QueueBasedIntentDeduper",
]
