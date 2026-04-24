"""Fill event dispatcher: raw HL payload dict → RawFillEvent with timestamps.

Single entry point `build_raw_event` used by both the WebSocket listener
and the REST reconciler. Keeps fill parsing in one place so field-name
drift in the HL API surfaces as one test failure, not twelve.

All timestamps are epoch milliseconds (not seconds) — match HL SDK.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from smart_money.signals.types import RawFillEvent, SourceKind

logger = logging.getLogger(__name__)


def now_ms() -> int:
    """Current wall-clock in epoch milliseconds."""
    return int(time.time() * 1000)


class DispatcherError(ValueError):
    """Raised when a fill dict is missing required fields — surface loudly."""


def build_raw_event(
    fill: dict[str, Any],
    wallet_address: str,
    source: SourceKind,
    ts_ws_received_ms: int | None = None,
) -> RawFillEvent:
    """Parse one HL fill dict into a RawFillEvent.

    Required fill fields (per HL userFills channel):
        tid, coin, px, sz, side, time, dir, fee

    Args:
        fill: raw dict from HL WS/REST.
        wallet_address: subscriber address, lowercased.
        source: "ws" if this came from the live WS channel, "reconciler" if
            from a REST catch-up sweep.
        ts_ws_received_ms: WS receipt timestamp (set by the listener callback
            in the WS thread, *before* the dispatcher runs — captures true
            network latency). If None (reconciler path), defaults to now.

    Raises:
        DispatcherError: if any required field is missing / unparseable.
    """
    try:
        ts_hl = int(fill["time"])
        side_raw = fill["side"]
        size_abs = float(fill["sz"])
        coin = fill["coin"]
        px = float(fill["px"])
        hl_trade_id = int(fill["tid"])
    except (KeyError, ValueError, TypeError) as e:
        raise DispatcherError(f"malformed HL fill payload: {e!r} — {fill!r}") from e

    if side_raw not in ("B", "A"):
        raise DispatcherError(f"unexpected side={side_raw!r} (expected B/A)")

    ts_ws = ts_ws_received_ms if ts_ws_received_ms is not None else now_ms()
    ts_queue = now_ms()

    # Sign the size so downstream consumers don't need to look at side_raw again.
    signed_size = size_abs if side_raw == "B" else -size_abs

    return RawFillEvent(
        wallet_address=wallet_address.lower(),
        symbol_hl=coin,
        side_raw=side_raw,
        direction_raw=str(fill.get("dir", "")),
        size=signed_size,
        px=px,
        fee=float(fill.get("fee", 0.0)),
        hl_trade_id=hl_trade_id,
        ts_hl_fill_ms=ts_hl,
        ts_ws_received_ms=ts_ws,
        ts_queue_processed_ms=ts_queue,
        source=source,
        # raw payload is chatty; keep only when someone actually reads it
        raw=fill if logger.isEnabledFor(logging.DEBUG) else None,
    )


__all__ = ["build_raw_event", "now_ms", "DispatcherError"]
