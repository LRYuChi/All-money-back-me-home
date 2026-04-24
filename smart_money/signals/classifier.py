"""Position state-machine classifier (P4b).

Turns a stateless `RawFillEvent` stream into stateful `Signal` events by
comparing each fill against the wallet's last known position on that symbol.

A single fill can mean five different things depending on prior state:
    flat + Open Long           → OPEN_LONG
    long 3 + Open Long 2       → SCALE_UP_LONG (new size = 5)
    long 5 + Close Long 2      → SCALE_DOWN_LONG (new size = 3)
    long 5 + Close Long 5      → CLOSE_LONG (new size = 0, side = flat)
    long 5 + "Long > Short" 3  → REVERSE_TO_SHORT (new side = short, size = 3)

HL quirks this handler is explicit about:
    - `direction_raw` carries the authoritative intent; `side_raw` (B/A) is
      merely the order side, not position side.
    - Reversal is sometimes one fill (`Long > Short`), sometimes two fills
      (Close Long + Open Short). We treat the one-fill form as REVERSE.
    - Cold start (prev=None): `Open *` trusted as fresh; `Close *` and
      reversal directions are treated as drift — we rebuild state from the
      fill but record a SkippedSignal with reason='cold_start_drift'.
    - Unrecognized directions (e.g. spot Buy/Sell): skipped, reason=
      'direction_unrecognized'. State is NOT updated.

Pure function: does not touch any store; caller persists the returned
WalletPosition and optional SkippedSignal themselves.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from smart_money.signals.types import RawFillEvent, Signal, SignalType
from smart_money.store.schema import PositionSide, SkippedSignal, WalletPosition

logger = logging.getLogger(__name__)

# Floating-point tolerance for "fully closed" detection. HL sizes are typically
# 3-6 decimal places; 1e-9 is well below meaningful precision.
_SIZE_EPS = 1e-9


@dataclass(slots=True)
class ClassifyResult:
    """Return type of `classify`. Exactly one of (signal, skipped) is set."""

    new_position: WalletPosition
    signal: Signal | None = None
    skipped: SkippedSignal | None = None


def classify(
    event: RawFillEvent,
    prev: WalletPosition | None,
    *,
    wallet_id: UUID,
    wallet_score: float = 0.0,
    whale_equity_usd: float | None = None,
) -> ClassifyResult:
    """Classify one fill against prior position state.

    Args:
        event: incoming fill.
        prev: last known position on this (wallet, symbol). None = cold start.
        wallet_id: UUID used to construct both the Signal and new WalletPosition.
        wallet_score: current score from sm_rankings, propagated for downstream
            aggregator weighting.
        whale_equity_usd: account equity at signal time (from clearinghouseState).
            Used by size-normalization downstream. Optional — may be None when
            the caller hasn't fetched it; the position math itself doesn't need it.

    Returns:
        ClassifyResult with updated position and either a Signal or a
        SkippedSignal (mutually exclusive) describing what happened.
    """
    direction = event.direction_raw.strip()
    abs_size = abs(event.size)
    fill_ts = _ts_to_datetime(event.ts_hl_fill_ms)

    # ---- Parse direction intent ------------------------------------
    intent = _parse_direction(direction)
    if intent is None:
        # Unknown direction — don't update state, record skip.
        skipped = SkippedSignal(
            wallet_id=wallet_id,
            wallet_address=event.wallet_address,
            symbol_hl=event.symbol_hl,
            reason="direction_unrecognized",
            signal_latency_ms=event.total_latency_ms,
            direction_raw=direction,
            hl_trade_id=event.hl_trade_id,
            detail={"side_raw": event.side_raw},
        )
        return ClassifyResult(
            new_position=prev or _empty_position(wallet_id, event.symbol_hl, fill_ts),
            skipped=skipped,
        )

    kind, target_side = intent  # kind: 'open' | 'close' | 'reverse'

    # ---- Cold start: no prior state ---------------------------------
    if prev is None or prev.side == "flat":
        if kind == "open":
            return _emit_open(event, wallet_id, wallet_score, target_side, abs_size, fill_ts, whale_equity_usd)
        # Cold start with close/reverse → drift.
        return _drift_skip(
            event, wallet_id, prev, fill_ts,
            reason="cold_start_drift",
            detail={"direction_raw": direction, "abs_size": abs_size},
        )

    # ---- Prior state exists; reconcile against intent ---------------
    if kind == "open":
        if prev.side == target_side:
            # scale-up: add to existing
            new_size = prev.size + abs_size
            # VWAP of entry price
            new_avg_px = (prev.size * (prev.avg_entry_px or event.px) + abs_size * event.px) / new_size
            sig_type = SignalType.SCALE_UP_LONG if target_side == "long" else SignalType.SCALE_UP_SHORT
            size_delta = abs_size
        else:
            # direction_raw said "Open X" but we're currently in opposite side.
            # HL should have emitted "X > Y" for a reversal; getting plain "Open"
            # means either our state is drifted or HL split the reverse across
            # two fills. Trust the new fill and treat as drift-resolving open
            # on the new side (previous position effectively orphaned).
            logger.warning(
                "classifier drift: wallet=%s sym=%s prev=%s %s direction=%s — "
                "rebuilding to %s %s",
                event.wallet_address[:10], event.symbol_hl, prev.side, prev.size,
                direction, target_side, abs_size,
            )
            new_size = abs_size
            new_avg_px = event.px
            sig_type = SignalType.OPEN_LONG if target_side == "long" else SignalType.OPEN_SHORT
            size_delta = abs_size

        signal = _build_signal(
            event, wallet_id, wallet_score, sig_type, size_delta,
            new_size=new_size, whale_equity_usd=whale_equity_usd,
        )
        new_pos = WalletPosition(
            wallet_id=wallet_id, symbol=event.symbol_hl,
            side=target_side, size=new_size,
            avg_entry_px=new_avg_px, last_updated_ts=fill_ts,
        )
        return ClassifyResult(new_position=new_pos, signal=signal)

    if kind == "close":
        if prev.side != target_side:
            # Closing a side we don't hold — drift.
            return _drift_skip(
                event, wallet_id, prev, fill_ts,
                reason="close_without_position",
                detail={"prev_side": prev.side, "direction_raw": direction},
            )

        remaining = prev.size - abs_size
        if remaining > _SIZE_EPS:
            # partial close
            sig_type = SignalType.SCALE_DOWN_LONG if target_side == "long" else SignalType.SCALE_DOWN_SHORT
            signal = _build_signal(
                event, wallet_id, wallet_score, sig_type, abs_size,
                new_size=remaining, whale_equity_usd=whale_equity_usd,
            )
            new_pos = WalletPosition(
                wallet_id=wallet_id, symbol=event.symbol_hl,
                side=target_side, size=remaining,
                avg_entry_px=prev.avg_entry_px,    # avg entry unchanged on scale-down
                last_updated_ts=fill_ts,
            )
            return ClassifyResult(new_position=new_pos, signal=signal)

        # full close (including over-close rounded to flat)
        sig_type = SignalType.CLOSE_LONG if target_side == "long" else SignalType.CLOSE_SHORT
        signal = _build_signal(
            event, wallet_id, wallet_score, sig_type, prev.size,
            new_size=0.0, whale_equity_usd=whale_equity_usd,
        )
        new_pos = WalletPosition(
            wallet_id=wallet_id, symbol=event.symbol_hl,
            side="flat", size=0.0, avg_entry_px=None, last_updated_ts=fill_ts,
        )
        return ClassifyResult(new_position=new_pos, signal=signal)

    # kind == 'reverse': "Long > Short" or "Short > Long"
    if prev.side != _opposite(target_side):
        # HL said "reverse from X" but we're not in X. Drift.
        return _drift_skip(
            event, wallet_id, prev, fill_ts,
            reason="reverse_without_matching_side",
            detail={"prev_side": prev.side, "direction_raw": direction},
        )

    sig_type = SignalType.REVERSE_TO_LONG if target_side == "long" else SignalType.REVERSE_TO_SHORT
    # HL's reverse-fill size is the SIZE OF THE NEW position (after the flip).
    # (Verified against observed fills — see test_classifier fixtures.)
    signal = _build_signal(
        event, wallet_id, wallet_score, sig_type, abs_size,
        new_size=abs_size, whale_equity_usd=whale_equity_usd,
    )
    new_pos = WalletPosition(
        wallet_id=wallet_id, symbol=event.symbol_hl,
        side=target_side, size=abs_size,
        avg_entry_px=event.px, last_updated_ts=fill_ts,
    )
    return ClassifyResult(new_position=new_pos, signal=signal)


# ================================================================== #
# Helpers
# ================================================================== #
def _parse_direction(direction: str) -> tuple[str, str] | None:
    """Return (intent, target_side) or None if unrecognized.

    intent: 'open' | 'close' | 'reverse'
    target_side: 'long' | 'short' — the side we end up on (for open/reverse)
                 or the side being closed (for close)
    """
    d = direction.strip()
    # Reversal first — catches both "Long > Short" and "Short > Long"
    if ">" in d:
        # "Long > Short" means position goes from long to short → target short
        parts = [p.strip() for p in d.split(">")]
        if len(parts) == 2:
            target = parts[1].lower()
            if target in ("long", "short"):
                return ("reverse", target)
        return None
    if d == "Open Long":
        return ("open", "long")
    if d == "Open Short":
        return ("open", "short")
    if d == "Close Long":
        return ("close", "long")
    if d == "Close Short":
        return ("close", "short")
    return None


def _opposite(side: str) -> PositionSide:
    return "short" if side == "long" else "long"


def _ts_to_datetime(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)


def _empty_position(wallet_id: UUID, symbol: str, ts: datetime) -> WalletPosition:
    return WalletPosition(
        wallet_id=wallet_id, symbol=symbol, side="flat",
        size=0.0, avg_entry_px=None, last_updated_ts=ts,
    )


def _emit_open(
    event: RawFillEvent,
    wallet_id: UUID,
    wallet_score: float,
    target_side: str,
    abs_size: float,
    fill_ts: datetime,
    whale_equity_usd: float | None,
) -> ClassifyResult:
    sig_type = SignalType.OPEN_LONG if target_side == "long" else SignalType.OPEN_SHORT
    signal = _build_signal(
        event, wallet_id, wallet_score, sig_type, abs_size,
        new_size=abs_size, whale_equity_usd=whale_equity_usd,
    )
    new_pos = WalletPosition(
        wallet_id=wallet_id, symbol=event.symbol_hl,
        side=target_side, size=abs_size,
        avg_entry_px=event.px, last_updated_ts=fill_ts,
    )
    return ClassifyResult(new_position=new_pos, signal=signal)


def _build_signal(
    event: RawFillEvent,
    wallet_id: UUID,
    wallet_score: float,
    sig_type: SignalType,
    size_delta: float,
    *,
    new_size: float,
    whale_equity_usd: float | None,
) -> Signal:
    whale_position_usd = new_size * event.px
    return Signal(
        wallet_id=wallet_id,
        wallet_address=event.wallet_address,
        wallet_score=wallet_score,
        symbol_hl=event.symbol_hl,
        signal_type=sig_type,
        size_delta=size_delta,
        new_size=new_size,
        px=event.px,
        whale_equity_usd=whale_equity_usd,
        whale_position_usd=whale_position_usd,
        source_event=event,
    )


def _drift_skip(
    event: RawFillEvent,
    wallet_id: UUID,
    prev: WalletPosition | None,
    fill_ts: datetime,
    *,
    reason: str,
    detail: dict,
) -> ClassifyResult:
    """Record a drift/skip. Position state is left unchanged (prev if any,
    else an 'empty flat' row so the caller has something to persist)."""
    logger.warning(
        "classifier drift: wallet=%s sym=%s reason=%s prev=%s detail=%s",
        event.wallet_address[:10], event.symbol_hl, reason,
        (prev.side if prev else None), detail,
    )
    skipped = SkippedSignal(
        wallet_id=wallet_id,
        wallet_address=event.wallet_address,
        symbol_hl=event.symbol_hl,
        reason=reason,
        signal_latency_ms=event.total_latency_ms,
        direction_raw=event.direction_raw,
        hl_trade_id=event.hl_trade_id,
        detail=detail,
    )
    new_pos = prev or _empty_position(wallet_id, event.symbol_hl, fill_ts)
    return ClassifyResult(new_position=new_pos, skipped=skipped)


__all__ = ["classify", "ClassifyResult"]
