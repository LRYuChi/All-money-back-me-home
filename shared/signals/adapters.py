"""Adapters: existing signal-producer types → UniversalSignal.

As Phase B rolls in, each signal source gets an adapter here so it can
write to `signal_history` alongside its own pipeline. No existing
behaviour changes — these are pure converters.

Sources covered:
  - Smart Money (smart_money.signals.types.Signal → UniversalSignal)
  - Kronos (Phase C — forecast DataFrame → UniversalSignal)
  - TA (Phase D — Supertrend state → UniversalSignal)
  - AI LLM (Phase D)
  - Macro (Phase D — confidence_engine sandboxes)
"""
from __future__ import annotations

from shared.signals.types import Direction, SignalSource, UniversalSignal


# ------------------------------------------------------------------ #
# Smart Money adapter (Phase B — now)
# ------------------------------------------------------------------ #
def from_smart_money(sm_signal) -> UniversalSignal:  # noqa: ANN001
    """Convert smart_money.signals.types.Signal → UniversalSignal.

    The import is deferred to avoid circular dependency: shared/ is a
    low-level shared package; smart_money/ depends on it, not vice versa.
    This helper lives here for discoverability but reads sm_signal
    structurally (duck-typed) — any object with the right attributes works.

    Field mapping:
        - source = SMART_MONEY
        - horizon = "15m" (SM is real-time whale fills)
        - direction inferred from SignalType (OPEN_LONG/SCALE_UP_* → long;
          OPEN_SHORT/SCALE_UP_* → short; CLOSE_* → neutral = exit;
          REVERSE_TO_LONG/SHORT → long/short of new side)
        - strength = wallet_score (from sm_rankings, already in [0,1])
        - symbol = canonical form "crypto:hyperliquid:{symbol_hl}"
          (whale positions are mirrored to OKX by L6 mapper layer, not here)
    """
    from smart_money.signals.types import SignalType

    st = sm_signal.signal_type
    if st in (SignalType.OPEN_LONG, SignalType.SCALE_UP_LONG, SignalType.REVERSE_TO_LONG):
        direction = Direction.LONG
    elif st in (SignalType.OPEN_SHORT, SignalType.SCALE_UP_SHORT, SignalType.REVERSE_TO_SHORT):
        direction = Direction.SHORT
    else:
        # CLOSE_LONG / CLOSE_SHORT / SCALE_DOWN_* — exit or trim events
        direction = Direction.NEUTRAL

    # Clamp wallet_score to [0,1] defensively — ranking may produce floats
    # slightly above 1.0 if weights aren't fully normalised.
    strength = max(0.0, min(1.0, sm_signal.wallet_score))

    return UniversalSignal(
        source=SignalSource.SMART_MONEY,
        symbol=f"crypto:hyperliquid:{sm_signal.symbol_hl}",
        horizon="15m",
        direction=direction,
        strength=strength,
        reason=f"whale {sm_signal.wallet_address[:10]} {st.value} "
               f"size_delta={sm_signal.size_delta:.4f}",
        details={
            "wallet_id": str(sm_signal.wallet_id),
            "signal_type": st.value,
            "size_delta": sm_signal.size_delta,
            "new_size": sm_signal.new_size,
            "px": sm_signal.px,
            "whale_position_usd": sm_signal.whale_position_usd,
            "whale_equity_usd": sm_signal.whale_equity_usd,
            "latency_ms": sm_signal.total_latency_ms,
            "hl_trade_id": sm_signal.source_event.hl_trade_id,
            "ts_hl_fill_ms": sm_signal.source_event.ts_hl_fill_ms,
        },
    )


__all__ = ["from_smart_money"]
