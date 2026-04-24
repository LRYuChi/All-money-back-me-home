"""Shared dataclasses and enums for the signal pipeline.

Pure types, no logic. Imported by dispatcher, classifier, aggregator, execution.

Layering:
    RawFillEvent   ← dispatcher output (WS/REST raw fills + timestamps)
    Signal         ← classifier output (position-state aware)
    FollowOrder    ← aggregator output (ready for execution layer)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal
from uuid import UUID


class SignalType(str, Enum):
    """Position-level event types produced by the classifier (P4b)."""

    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    SCALE_UP_LONG = "scale_up_long"
    SCALE_UP_SHORT = "scale_up_short"
    SCALE_DOWN_LONG = "scale_down_long"
    SCALE_DOWN_SHORT = "scale_down_short"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    REVERSE_TO_LONG = "reverse_to_long"
    REVERSE_TO_SHORT = "reverse_to_short"


SourceKind = Literal["ws", "reconciler"]


@dataclass(frozen=True, slots=True)
class RawFillEvent:
    """A single HL fill with three capture timestamps.

    Emitted by the dispatcher. Consumers MUST treat this as immutable and key
    deduplication off `hl_trade_id`.

    Timestamps are epoch milliseconds (not seconds) — consistent with HL SDK.
    """

    wallet_address: str           # lowercased EVM hex (0x…)
    symbol_hl: str                # e.g. "BTC", "ETH"
    side_raw: Literal["B", "A"]   # HL convention: B = buy (long-leaning), A = sell
    direction_raw: str            # HL "dir" field verbatim: "Open Long" / "Close Short" / "Long > Short" / …
    size: float                   # signed coin units — positive for buy, negative for sell
    px: float                     # fill price
    fee: float                    # fee in USDC
    hl_trade_id: int              # HL "tid" — unique per (wallet, trade)
    ts_hl_fill_ms: int            # HL's fill timestamp (on-chain settlement)
    ts_ws_received_ms: int        # when our WS client received the message
    ts_queue_processed_ms: int    # when dispatcher put the event on the queue
    source: SourceKind
    raw: dict[str, Any] | None = None  # original payload, kept only when log level = DEBUG

    @property
    def total_latency_ms(self) -> int:
        """End-to-end: HL fill → our queue."""
        return self.ts_queue_processed_ms - self.ts_hl_fill_ms

    @property
    def network_latency_ms(self) -> int:
        """HL fill → WS message received. Negative if clocks are skewed."""
        return self.ts_ws_received_ms - self.ts_hl_fill_ms

    @property
    def processing_latency_ms(self) -> int:
        """WS received → queued. Should be sub-millisecond."""
        return self.ts_queue_processed_ms - self.ts_ws_received_ms


@dataclass(frozen=True, slots=True)
class Signal:
    """Position-level event emitted by the classifier (P4b).

    Differs from RawFillEvent by incorporating *position state*: a raw fill
    that merely reduces a long from 10 → 8 becomes SCALE_DOWN_LONG here.
    """

    wallet_id: UUID
    wallet_address: str
    wallet_score: float              # from sm_rankings at signal time
    symbol_hl: str
    signal_type: SignalType
    size_delta: float                # absolute magnitude of position change (coin units)
    new_size: float                  # absolute size after the fill (0 = closed)
    px: float
    whale_equity_usd: float | None   # from clearinghouseState; None if not yet fetched
    whale_position_usd: float        # post-fill notional on this symbol
    source_event: RawFillEvent       # underlying fill for audit + latency trace

    @property
    def total_latency_ms(self) -> int:
        return self.source_event.total_latency_ms


FollowAction = Literal["open", "close", "scale"]
FollowSide = Literal["buy", "sell"]


@dataclass(frozen=True, slots=True)
class FollowOrder:
    """Aggregator output — fully translated, ready for execution guards + order layer."""

    symbol_okx: str
    side: FollowSide
    action: FollowAction
    size_coin: float                 # OKX unit after mapper conversion
    size_notional_usd: float
    source_signals: tuple[Signal, ...]  # one or more (if aggregated)
    client_order_id: str             # idempotency key for OKX
    created_ts_ms: int


__all__ = [
    "SignalType",
    "SourceKind",
    "RawFillEvent",
    "Signal",
    "FollowOrder",
    "FollowAction",
    "FollowSide",
]
