"""Tests for smart_money.signals.classifier (P4b)."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from smart_money.signals.classifier import classify
from smart_money.signals.types import RawFillEvent, SignalType
from smart_money.store.schema import WalletPosition


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
def make_event(
    *,
    tid: int = 1,
    coin: str = "BTC",
    direction: str = "Open Long",
    size: float = 0.5,           # signed (B = +, A = -)
    px: float = 50_000.0,
    ts_hl_fill_ms: int = 1_700_000_000_000,
) -> RawFillEvent:
    return RawFillEvent(
        wallet_address="0xabc",
        symbol_hl=coin,
        side_raw="B" if size > 0 else "A",
        direction_raw=direction,
        size=size,
        px=px,
        fee=1.0,
        hl_trade_id=tid,
        ts_hl_fill_ms=ts_hl_fill_ms,
        ts_ws_received_ms=ts_hl_fill_ms + 500,
        ts_queue_processed_ms=ts_hl_fill_ms + 550,
        source="ws",
    )


def make_position(
    side: str = "long",
    size: float = 1.0,
    avg_px: float | None = 50_000.0,
    symbol: str = "BTC",
    wallet_id=None,
) -> WalletPosition:
    return WalletPosition(
        wallet_id=wallet_id or uuid4(),
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        size=size,
        avg_entry_px=avg_px,
        last_updated_ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def wallet_id():
    return uuid4()


# ------------------------------------------------------------------ #
# Cold start
# ------------------------------------------------------------------ #
def test_cold_start_open_long_emits_open_long(wallet_id):
    event = make_event(direction="Open Long", size=0.5)
    result = classify(event, prev=None, wallet_id=wallet_id)

    assert result.signal is not None
    assert result.signal.signal_type == SignalType.OPEN_LONG
    assert result.signal.size_delta == 0.5
    assert result.signal.new_size == 0.5
    assert result.new_position.side == "long"
    assert result.new_position.size == 0.5
    assert result.new_position.avg_entry_px == 50_000.0
    assert result.skipped is None


def test_cold_start_open_short_emits_open_short(wallet_id):
    event = make_event(direction="Open Short", size=-0.5)
    result = classify(event, prev=None, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.OPEN_SHORT
    assert result.new_position.side == "short"


def test_cold_start_close_is_drift_skipped(wallet_id):
    event = make_event(direction="Close Long", size=-0.5)
    result = classify(event, prev=None, wallet_id=wallet_id)

    assert result.signal is None
    assert result.skipped is not None
    assert result.skipped.reason == "cold_start_drift"
    # Position remains flat (no update)
    assert result.new_position.side == "flat"


def test_cold_start_reversal_is_drift_skipped(wallet_id):
    event = make_event(direction="Long > Short", size=-0.5)
    result = classify(event, prev=None, wallet_id=wallet_id)

    assert result.signal is None
    assert result.skipped.reason == "cold_start_drift"


def test_flat_position_treated_as_cold_start(wallet_id):
    prev = make_position(side="flat", size=0.0, avg_px=None, wallet_id=wallet_id)
    event = make_event(direction="Open Long", size=0.3)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.OPEN_LONG
    assert result.new_position.side == "long"


# ------------------------------------------------------------------ #
# Scale up
# ------------------------------------------------------------------ #
def test_long_plus_open_long_scales_up(wallet_id):
    prev = make_position(side="long", size=1.0, avg_px=40_000.0, wallet_id=wallet_id)
    event = make_event(direction="Open Long", size=1.0, px=60_000.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.SCALE_UP_LONG
    assert result.signal.size_delta == 1.0
    assert result.new_position.size == 2.0
    # VWAP of entry: (1*40000 + 1*60000) / 2 = 50000
    assert result.new_position.avg_entry_px == 50_000.0


def test_short_plus_open_short_scales_up(wallet_id):
    prev = make_position(side="short", size=2.0, avg_px=3_000.0, wallet_id=wallet_id)
    event = make_event(coin="ETH", direction="Open Short", size=-1.0, px=2_000.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.SCALE_UP_SHORT
    assert result.new_position.size == 3.0
    # VWAP: (2*3000 + 1*2000) / 3 = 2666.67
    assert abs(result.new_position.avg_entry_px - 2_666.666_666_666_7) < 1e-6


# ------------------------------------------------------------------ #
# Scale down (partial close)
# ------------------------------------------------------------------ #
def test_long_minus_partial_close_scales_down(wallet_id):
    prev = make_position(side="long", size=2.0, avg_px=50_000.0, wallet_id=wallet_id)
    event = make_event(direction="Close Long", size=-0.5)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.SCALE_DOWN_LONG
    assert result.signal.size_delta == 0.5
    assert result.new_position.size == 1.5
    # avg_entry_px unchanged on partial close
    assert result.new_position.avg_entry_px == 50_000.0


def test_short_minus_partial_close_scales_down(wallet_id):
    prev = make_position(side="short", size=3.0, wallet_id=wallet_id)
    event = make_event(direction="Close Short", size=0.5)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.SCALE_DOWN_SHORT
    assert result.new_position.size == 2.5


# ------------------------------------------------------------------ #
# Full close
# ------------------------------------------------------------------ #
def test_long_minus_full_close_transitions_to_flat(wallet_id):
    prev = make_position(side="long", size=1.0, avg_px=50_000.0, wallet_id=wallet_id)
    event = make_event(direction="Close Long", size=-1.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.CLOSE_LONG
    assert result.signal.size_delta == 1.0
    assert result.signal.new_size == 0.0
    assert result.new_position.side == "flat"
    assert result.new_position.size == 0.0
    assert result.new_position.avg_entry_px is None


def test_close_slightly_over_size_rounds_to_flat(wallet_id):
    """Floating-point drift: closing 1.0 + 1e-10 of a 1.0 position should be flat, not -1e-10 long."""
    prev = make_position(side="long", size=1.0, wallet_id=wallet_id)
    event = make_event(direction="Close Long", size=-(1.0 + 1e-10))
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.CLOSE_LONG
    assert result.new_position.side == "flat"


# ------------------------------------------------------------------ #
# Reversal (one-fill form)
# ------------------------------------------------------------------ #
def test_long_to_short_reversal(wallet_id):
    prev = make_position(side="long", size=2.0, avg_px=50_000.0, wallet_id=wallet_id)
    event = make_event(direction="Long > Short", size=-1.5, px=48_000.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.REVERSE_TO_SHORT
    assert result.signal.size_delta == 1.5
    assert result.signal.new_size == 1.5
    assert result.new_position.side == "short"
    assert result.new_position.size == 1.5
    assert result.new_position.avg_entry_px == 48_000.0  # reset to reversal price


def test_short_to_long_reversal(wallet_id):
    prev = make_position(side="short", size=3.0, wallet_id=wallet_id)
    event = make_event(direction="Short > Long", size=2.0, px=40_000.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal.signal_type == SignalType.REVERSE_TO_LONG
    assert result.new_position.side == "long"
    assert result.new_position.size == 2.0


def test_reverse_without_matching_side_is_drift(wallet_id):
    """HL says 'Long > Short' but we're currently flat — can't be right."""
    prev = make_position(side="flat", size=0.0, avg_px=None, wallet_id=wallet_id)
    event = make_event(direction="Long > Short", size=-1.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    # Flat + reversal → cold-start drift (flat is treated as cold start)
    assert result.signal is None
    assert result.skipped.reason == "cold_start_drift"


def test_reverse_to_long_while_currently_long_is_drift(wallet_id):
    """HL says 'Short > Long' but we're already long — inconsistency."""
    prev = make_position(side="long", size=1.0, wallet_id=wallet_id)
    event = make_event(direction="Short > Long", size=2.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal is None
    assert result.skipped.reason == "reverse_without_matching_side"


# ------------------------------------------------------------------ #
# Drift: direction doesn't match current state
# ------------------------------------------------------------------ #
def test_close_long_without_long_position_is_drift(wallet_id):
    prev = make_position(side="short", size=1.0, wallet_id=wallet_id)
    event = make_event(direction="Close Long", size=-0.5)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal is None
    assert result.skipped.reason == "close_without_position"
    # State unchanged (still short 1.0)
    assert result.new_position.side == "short"
    assert result.new_position.size == 1.0


def test_open_long_while_short_rebuilds_state(wallet_id):
    """HL split a reversal into two fills (close short + open long) but we only
    see the second — state gets rebuilt on the new side."""
    prev = make_position(side="short", size=2.0, wallet_id=wallet_id)
    event = make_event(direction="Open Long", size=1.5, px=45_000.0)
    result = classify(event, prev=prev, wallet_id=wallet_id)

    # Signal emitted as fresh OPEN (even though drift-log is recorded at WARN)
    assert result.signal.signal_type == SignalType.OPEN_LONG
    assert result.new_position.side == "long"
    assert result.new_position.size == 1.5
    assert result.new_position.avg_entry_px == 45_000.0


# ------------------------------------------------------------------ #
# Unrecognized direction
# ------------------------------------------------------------------ #
def test_unrecognized_direction_is_skipped_without_state_change(wallet_id):
    prev = make_position(side="long", size=1.0, avg_px=50_000.0, wallet_id=wallet_id)
    event = make_event(direction="Buy", size=0.1)  # spot, we don't handle
    result = classify(event, prev=prev, wallet_id=wallet_id)

    assert result.signal is None
    assert result.skipped.reason == "direction_unrecognized"
    # Prior state preserved unchanged
    assert result.new_position is prev  # same object, no mutation


# ------------------------------------------------------------------ #
# Signal metadata
# ------------------------------------------------------------------ #
def test_signal_inherits_source_event_for_latency_trace(wallet_id):
    event = make_event(direction="Open Long", size=0.5)
    result = classify(event, prev=None, wallet_id=wallet_id)

    assert result.signal.source_event is event
    assert result.signal.total_latency_ms == event.total_latency_ms


def test_signal_carries_wallet_score(wallet_id):
    event = make_event(direction="Open Long", size=0.5)
    result = classify(event, prev=None, wallet_id=wallet_id, wallet_score=0.87)

    assert result.signal.wallet_score == 0.87


def test_whale_equity_propagated_to_signal(wallet_id):
    event = make_event(direction="Open Long", size=0.5, px=50_000.0)
    result = classify(event, prev=None, wallet_id=wallet_id, whale_equity_usd=1_000_000.0)

    assert result.signal.whale_equity_usd == 1_000_000.0
    # whale_position_usd = new_size * px = 0.5 * 50000 = 25000
    assert result.signal.whale_position_usd == 25_000.0


# ------------------------------------------------------------------ #
# Determinism: same input → same output
# ------------------------------------------------------------------ #
def test_determinism(wallet_id):
    prev = make_position(side="long", size=1.0, avg_px=50_000.0, wallet_id=wallet_id)
    event = make_event(direction="Open Long", size=0.5, px=60_000.0)

    r1 = classify(event, prev=prev, wallet_id=wallet_id)
    r2 = classify(event, prev=prev, wallet_id=wallet_id)

    assert r1.signal.signal_type == r2.signal.signal_type
    assert r1.new_position.size == r2.new_position.size
    assert r1.new_position.avg_entry_px == r2.new_position.avg_entry_px
