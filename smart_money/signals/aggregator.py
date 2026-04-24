"""Signal aggregator (P4c).

Converts `Signal` events into `FollowOrder` dispatches.

Two modes:
    independent  — 1 Signal → 1 FollowOrder. Simple, deterministic, but
                   every whale's entry independently sizes a position on
                   our side, risking over-exposure when 3 whales all open
                   BTC longs within a minute of each other.

    aggregated   — accumulate Signal(OPEN_*) in a time window; emit a
                   single FollowOrder only when ≥ `min_wallets` distinct
                   wallets agree on direction. size_mult scales the notional
                   by the sum of wallet scores, clamped downstream by
                   execution guards.

Aggregation only applies to OPEN_LONG / OPEN_SHORT. CLOSE_*, SCALE_*,
REVERSE_* are always independent — they're wallet-specific position
adjustments, not market-direction votes.

Pure state machine: no side effects, no clock. Callers pass `now_ms`
to `ingest()` and `flush_expired()`. This keeps tests trivially
deterministic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from smart_money.signals.types import (
    FollowAction,
    FollowOrder,
    FollowSide,
    Signal,
    SignalType,
)

logger = logging.getLogger(__name__)


AggregationMode = Literal["independent", "aggregated"]


# Signal types that map to an 'open' FollowOrder.
_OPEN_TYPES = {SignalType.OPEN_LONG, SignalType.OPEN_SHORT}

# Signal types that collapse to 'close' (P4c: SCALE_DOWN treated as partial
# close in shadow simulator; REVERSE treated as close + open pair).
_CLOSE_TYPES = {
    SignalType.CLOSE_LONG,
    SignalType.CLOSE_SHORT,
    SignalType.SCALE_DOWN_LONG,
    SignalType.SCALE_DOWN_SHORT,
}

# Signal types that increase existing exposure — 'scale' action.
_SCALE_TYPES = {
    SignalType.SCALE_UP_LONG,
    SignalType.SCALE_UP_SHORT,
}

# Reversal is emitted as two FollowOrders: close + open.
_REVERSE_TYPES = {
    SignalType.REVERSE_TO_LONG,
    SignalType.REVERSE_TO_SHORT,
}


@dataclass(slots=True)
class PendingBucket:
    """Accumulator for OPEN signals in aggregated mode."""

    signals_by_wallet: dict[UUID, tuple[Signal, int]] = field(default_factory=dict)
    first_seen_ms: int = 0

    def add(self, signal: Signal, now_ms: int) -> None:
        # Replace on same wallet — whale may have changed their entry price within window
        if not self.signals_by_wallet:
            self.first_seen_ms = now_ms
        self.signals_by_wallet[signal.wallet_id] = (signal, now_ms)

    def distinct_wallets(self) -> int:
        return len(self.signals_by_wallet)

    def signals(self) -> list[Signal]:
        return [s for s, _ in self.signals_by_wallet.values()]


class SignalAggregator:
    """Stateful aggregator. Not thread-safe — use from one asyncio task."""

    def __init__(
        self,
        mode: AggregationMode,
        *,
        window_sec: int = 300,
        min_wallets: int = 2,
        score_baseline: float = 0.6,
    ) -> None:
        if min_wallets < 1:
            raise ValueError(f"min_wallets must be >= 1, got {min_wallets}")
        self._mode = mode
        self._window_ms = window_sec * 1000
        self._min_wallets = min_wallets
        self._score_baseline = score_baseline
        # (symbol_hl, target_side) → PendingBucket
        self._pending: dict[tuple[str, FollowSide], PendingBucket] = {}

    # ---------------------------------------------------------------- #
    # Public API
    # ---------------------------------------------------------------- #
    def ingest(self, signal: Signal, now_ms: int) -> list[FollowOrder]:
        """Process one signal. Return FollowOrders ready to emit (0+).

        Aggregated mode for OPEN signals may return 0 (still accumulating)
        or 1 (threshold hit); other signal types always return 1 (or 2 for
        reversal) regardless of mode.
        """
        if signal.signal_type in _OPEN_TYPES and self._mode == "aggregated":
            return self._ingest_aggregated_open(signal, now_ms)

        if signal.signal_type in _REVERSE_TYPES:
            return self._ingest_reverse(signal, now_ms)

        # Everything else: independent emit
        order = self._emit_single(signal, now_ms)
        return [order] if order else []

    def flush_expired(self, now_ms: int) -> list[FollowOrder]:
        """Drop pending buckets whose window has elapsed without hitting
        min_wallets. Returns empty list — this is cleanup, not emit.

        Called by the daemon on a periodic timer to bound memory + prevent
        stale signals from being aggregated with much-later ones.
        """
        expired_keys: list[tuple[str, FollowSide]] = []
        for key, bucket in self._pending.items():
            if now_ms - bucket.first_seen_ms >= self._window_ms:
                expired_keys.append(key)
        for key in expired_keys:
            bucket = self._pending.pop(key)
            logger.info(
                "aggregator: dropped expired bucket %s (%d wallets, waited %.1fs)",
                key, bucket.distinct_wallets(),
                (now_ms - bucket.first_seen_ms) / 1000,
            )
        return []

    # ---------------------------------------------------------------- #
    # Aggregated OPEN path
    # ---------------------------------------------------------------- #
    def _ingest_aggregated_open(self, signal: Signal, now_ms: int) -> list[FollowOrder]:
        target_side = _side_from_open(signal.signal_type)
        key = (signal.symbol_hl, target_side)
        bucket = self._pending.setdefault(key, PendingBucket())
        bucket.add(signal, now_ms)

        # Prune: if the bucket's first_seen is outside the window, reset.
        if now_ms - bucket.first_seen_ms > self._window_ms:
            bucket.signals_by_wallet = {signal.wallet_id: (signal, now_ms)}
            bucket.first_seen_ms = now_ms

        if bucket.distinct_wallets() < self._min_wallets:
            return []

        # Threshold hit — emit
        sigs = bucket.signals()
        del self._pending[key]

        size_mult = self._aggregated_size_mult(sigs)
        return [self._build_open_order(sigs, target_side, now_ms, size_mult=size_mult)]

    def _aggregated_size_mult(self, signals: list[Signal]) -> float:
        """Sum of wallet scores divided by baseline — defines the size ratio
        relative to a single-baseline-score signal."""
        total_score = sum(s.wallet_score for s in signals)
        if self._score_baseline <= 0:
            return 1.0
        return max(0.0, total_score / self._score_baseline)

    # ---------------------------------------------------------------- #
    # Reversal
    # ---------------------------------------------------------------- #
    def _ingest_reverse(self, signal: Signal, now_ms: int) -> list[FollowOrder]:
        """A reversal is two FollowOrders: close existing + open new."""
        new_side = "buy" if signal.signal_type == SignalType.REVERSE_TO_LONG else "sell"
        # Close leg: the previous direction (opposite of new_side)
        close_leg = FollowOrder(
            symbol_okx="",                           # P5 mapper fills this
            side="sell" if new_side == "buy" else "buy",
            action="close",
            size_coin=signal.size_delta,
            size_notional_usd=signal.size_delta * signal.px,
            source_signals=(signal,),
            client_order_id=_cloid(signal, "close", now_ms),
            created_ts_ms=now_ms,
        )
        open_leg = FollowOrder(
            symbol_okx="",
            side=new_side,
            action="open",
            size_coin=signal.new_size,
            size_notional_usd=signal.new_size * signal.px,
            source_signals=(signal,),
            client_order_id=_cloid(signal, "open", now_ms + 1),  # +1 to differ
            created_ts_ms=now_ms,
        )
        return [close_leg, open_leg]

    # ---------------------------------------------------------------- #
    # Independent / non-open signals
    # ---------------------------------------------------------------- #
    def _emit_single(self, signal: Signal, now_ms: int) -> FollowOrder | None:
        action, side = _action_side_for(signal.signal_type)
        if action is None or side is None:
            return None
        return FollowOrder(
            symbol_okx="",
            side=side,
            action=action,
            size_coin=signal.size_delta,
            size_notional_usd=signal.size_delta * signal.px,
            source_signals=(signal,),
            client_order_id=_cloid(signal, action, now_ms),
            created_ts_ms=now_ms,
        )

    def _build_open_order(
        self,
        signals: list[Signal],
        target_side: FollowSide,
        now_ms: int,
        *,
        size_mult: float,
    ) -> FollowOrder:
        """Aggregated-mode open: merge size weighted by score."""
        # Use the most recent signal's px as reference (mid-window)
        latest = max(signals, key=lambda s: s.source_event.ts_hl_fill_ms)
        merged_size = sum(s.size_delta for s in signals) * size_mult / max(len(signals), 1)
        return FollowOrder(
            symbol_okx="",
            side=target_side,
            action="open",
            size_coin=merged_size,
            size_notional_usd=merged_size * latest.px,
            source_signals=tuple(signals),
            client_order_id=_cloid(latest, "open_agg", now_ms),
            created_ts_ms=now_ms,
        )


# ================================================================== #
# Helpers
# ================================================================== #
def _side_from_open(sig_type: SignalType) -> FollowSide:
    return "buy" if sig_type == SignalType.OPEN_LONG else "sell"


def _action_side_for(sig_type: SignalType) -> tuple[FollowAction | None, FollowSide | None]:
    """Map SignalType → (action, OKX order side)."""
    if sig_type == SignalType.OPEN_LONG:
        return ("open", "buy")
    if sig_type == SignalType.OPEN_SHORT:
        return ("open", "sell")
    if sig_type in (SignalType.CLOSE_LONG, SignalType.SCALE_DOWN_LONG):
        return ("close", "sell")
    if sig_type in (SignalType.CLOSE_SHORT, SignalType.SCALE_DOWN_SHORT):
        return ("close", "buy")
    if sig_type == SignalType.SCALE_UP_LONG:
        return ("scale", "buy")
    if sig_type == SignalType.SCALE_UP_SHORT:
        return ("scale", "sell")
    return (None, None)


def _cloid(signal: Signal, action: str, now_ms: int) -> str:
    """Deterministic client order id — keeps retries idempotent at OKX."""
    addr10 = signal.wallet_address[:10]
    return f"sm-{addr10}-{signal.symbol_hl}-{action}-{now_ms}"


__all__ = [
    "AggregationMode",
    "SignalAggregator",
    "PendingBucket",
]
