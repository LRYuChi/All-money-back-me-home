"""Tests for smart_money.signals.dispatcher + types (P4a)."""
from __future__ import annotations

import pytest

from smart_money.signals.dispatcher import DispatcherError, build_raw_event, now_ms
from smart_money.signals.types import RawFillEvent


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
def make_hl_fill(
    *,
    tid: int = 101,
    coin: str = "BTC",
    px: float = 50_000.0,
    sz: float = 0.5,
    side: str = "B",
    direction: str = "Open Long",
    time_ms: int = 1_700_000_000_000,
    fee: float = 2.5,
) -> dict:
    """Mirror the HL userFills fill schema."""
    return {
        "tid": tid,
        "coin": coin,
        "px": str(px),
        "sz": str(sz),
        "side": side,
        "time": time_ms,
        "dir": direction,
        "fee": str(fee),
        "closedPnl": "0",
        "hash": "0xdeadbeef",
        "oid": 9999,
        "crossed": True,
        "startPosition": "0",
    }


# ------------------------------------------------------------------ #
# now_ms
# ------------------------------------------------------------------ #
def test_now_ms_returns_epoch_ms_not_seconds():
    t = now_ms()
    # Sanity: after 2023 and before 2100
    assert t > 1_700_000_000_000
    assert t < 4_000_000_000_000


# ------------------------------------------------------------------ #
# build_raw_event — happy path
# ------------------------------------------------------------------ #
def test_build_raw_event_parses_buy_as_positive_size():
    fill = make_hl_fill(side="B", sz=0.5)
    event = build_raw_event(fill, "0xABC", source="ws")

    assert event.size == 0.5
    assert event.side_raw == "B"


def test_build_raw_event_parses_sell_as_negative_size():
    fill = make_hl_fill(side="A", sz=0.5)
    event = build_raw_event(fill, "0xABC", source="ws")

    assert event.size == -0.5
    assert event.side_raw == "A"


def test_build_raw_event_lowercases_wallet_address():
    fill = make_hl_fill()
    event = build_raw_event(fill, "0xAbCdEf0123456789", source="ws")
    assert event.wallet_address == "0xabcdef0123456789"


def test_build_raw_event_preserves_direction_verbatim():
    fill = make_hl_fill(direction="Close Short")
    event = build_raw_event(fill, "0xabc", source="ws")
    assert event.direction_raw == "Close Short"


def test_build_raw_event_preserves_reversal_direction():
    fill = make_hl_fill(direction="Long > Short")
    event = build_raw_event(fill, "0xabc", source="ws")
    assert event.direction_raw == "Long > Short"


def test_build_raw_event_source_ws_vs_reconciler():
    fill = make_hl_fill()
    ws_ev = build_raw_event(fill, "0xabc", source="ws")
    rec_ev = build_raw_event(fill, "0xabc", source="reconciler")
    assert ws_ev.source == "ws"
    assert rec_ev.source == "reconciler"


# ------------------------------------------------------------------ #
# Timestamps — the whole point of P4a
# ------------------------------------------------------------------ #
def test_timestamps_fill_ts_matches_input():
    fill = make_hl_fill(time_ms=1_700_123_456_789)
    event = build_raw_event(fill, "0xabc", source="ws", ts_ws_received_ms=1_700_123_500_000)
    assert event.ts_hl_fill_ms == 1_700_123_456_789


def test_timestamps_ws_received_from_caller():
    fill = make_hl_fill(time_ms=1_700_000_000_000)
    event = build_raw_event(fill, "0xabc", source="ws", ts_ws_received_ms=1_700_000_003_000)
    assert event.ts_ws_received_ms == 1_700_000_003_000


def test_timestamps_queue_processed_is_now():
    fill = make_hl_fill()
    before = now_ms()
    event = build_raw_event(fill, "0xabc", source="ws")
    after = now_ms()
    assert before <= event.ts_queue_processed_ms <= after


def test_latency_properties_compute_correctly():
    fill = make_hl_fill(time_ms=1_700_000_000_000)
    event = build_raw_event(
        fill, "0xabc", source="ws", ts_ws_received_ms=1_700_000_002_500
    )
    # Force deterministic queue time by constructing a fresh event
    manually = RawFillEvent(
        wallet_address=event.wallet_address,
        symbol_hl=event.symbol_hl,
        side_raw=event.side_raw,
        direction_raw=event.direction_raw,
        size=event.size,
        px=event.px,
        fee=event.fee,
        hl_trade_id=event.hl_trade_id,
        ts_hl_fill_ms=1_700_000_000_000,
        ts_ws_received_ms=1_700_000_002_500,
        ts_queue_processed_ms=1_700_000_002_800,
        source="ws",
    )
    assert manually.network_latency_ms == 2_500
    assert manually.processing_latency_ms == 300
    assert manually.total_latency_ms == 2_800


def test_reconciler_source_defaults_ws_received_to_now():
    """When source=reconciler and no ts_ws_received_ms given, default is 'now'."""
    fill = make_hl_fill(time_ms=1_700_000_000_000)
    before = now_ms()
    event = build_raw_event(fill, "0xabc", source="reconciler")
    after = now_ms()
    # ts_ws_received_ms defaults to now
    assert before <= event.ts_ws_received_ms <= after


# ------------------------------------------------------------------ #
# Error handling
# ------------------------------------------------------------------ #
def test_missing_required_field_raises_dispatcher_error():
    bad = {"coin": "BTC", "px": "50000", "sz": "0.5", "side": "B"}  # no tid, no time
    with pytest.raises(DispatcherError):
        build_raw_event(bad, "0xabc", source="ws")


def test_invalid_side_raises_dispatcher_error():
    fill = make_hl_fill(side="X")
    with pytest.raises(DispatcherError, match="unexpected side"):
        build_raw_event(fill, "0xabc", source="ws")


def test_unparseable_price_raises_dispatcher_error():
    fill = make_hl_fill()
    fill["px"] = "not-a-number"
    with pytest.raises(DispatcherError):
        build_raw_event(fill, "0xabc", source="ws")


# ------------------------------------------------------------------ #
# Immutability (frozen dataclass)
# ------------------------------------------------------------------ #
def test_raw_fill_event_is_frozen():
    fill = make_hl_fill()
    event = build_raw_event(fill, "0xabc", source="ws")
    with pytest.raises(Exception):
        event.size = 999  # type: ignore[misc]


# ------------------------------------------------------------------ #
# Raw payload retention — only kept at DEBUG for memory discipline
# ------------------------------------------------------------------ #
def test_raw_payload_dropped_at_info_level(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="smart_money.signals.dispatcher")
    fill = make_hl_fill()
    event = build_raw_event(fill, "0xabc", source="ws")
    assert event.raw is None


def test_raw_payload_kept_at_debug_level(caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="smart_money.signals.dispatcher")
    fill = make_hl_fill()
    event = build_raw_event(fill, "0xabc", source="ws")
    assert event.raw == fill
