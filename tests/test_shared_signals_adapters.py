"""Tests for shared.signals.adapters — source → UniversalSignal converters."""
from __future__ import annotations

from uuid import uuid4

import pytest

from shared.signals.adapters import from_smart_money
from shared.signals.types import Direction, SignalSource
from smart_money.signals.types import RawFillEvent, Signal, SignalType


def make_sm_signal(
    *,
    sig_type: SignalType = SignalType.OPEN_LONG,
    size_delta: float = 0.5,
    new_size: float = 0.5,
    px: float = 50_000.0,
    wallet_score: float = 0.7,
    symbol: str = "BTC",
) -> Signal:
    event = RawFillEvent(
        wallet_address="0xabc1234567890",
        symbol_hl=symbol, side_raw="B", direction_raw="Open Long",
        size=size_delta, px=px, fee=1.0, hl_trade_id=42,
        ts_hl_fill_ms=1_700_000_000_000,
        ts_ws_received_ms=1_700_000_000_500,
        ts_queue_processed_ms=1_700_000_000_550,
        source="ws",
    )
    return Signal(
        wallet_id=uuid4(),
        wallet_address="0xabc1234567890",
        wallet_score=wallet_score,
        symbol_hl=symbol,
        signal_type=sig_type,
        size_delta=size_delta,
        new_size=new_size,
        px=px,
        whale_equity_usd=1_000_000.0,
        whale_position_usd=new_size * px,
        source_event=event,
    )


# ------------------------------------------------------------------ #
# Direction mapping — all 10 SignalTypes must map deterministically
# ------------------------------------------------------------------ #
@pytest.mark.parametrize("st,expected_dir", [
    (SignalType.OPEN_LONG, Direction.LONG),
    (SignalType.OPEN_SHORT, Direction.SHORT),
    (SignalType.SCALE_UP_LONG, Direction.LONG),
    (SignalType.SCALE_UP_SHORT, Direction.SHORT),
    (SignalType.SCALE_DOWN_LONG, Direction.NEUTRAL),
    (SignalType.SCALE_DOWN_SHORT, Direction.NEUTRAL),
    (SignalType.CLOSE_LONG, Direction.NEUTRAL),
    (SignalType.CLOSE_SHORT, Direction.NEUTRAL),
    (SignalType.REVERSE_TO_LONG, Direction.LONG),
    (SignalType.REVERSE_TO_SHORT, Direction.SHORT),
])
def test_sm_signal_type_maps_to_direction(st, expected_dir):
    sm = make_sm_signal(sig_type=st)
    u = from_smart_money(sm)
    assert u.direction == expected_dir


# ------------------------------------------------------------------ #
# Metadata preservation
# ------------------------------------------------------------------ #
def test_adapter_sets_source_and_horizon():
    sm = make_sm_signal()
    u = from_smart_money(sm)
    assert u.source == SignalSource.SMART_MONEY
    assert u.horizon == "15m"


def test_canonical_symbol_format():
    sm = make_sm_signal(symbol="ETH")
    u = from_smart_money(sm)
    assert u.symbol == "crypto:hyperliquid:ETH"


def test_wallet_score_becomes_strength():
    sm = make_sm_signal(wallet_score=0.42)
    u = from_smart_money(sm)
    assert u.strength == 0.42


def test_wallet_score_clamped_above_1():
    # Defensive: ranking may produce slightly-above-1 scores if weights
    # aren't fully normalised
    sm = make_sm_signal(wallet_score=1.15)
    u = from_smart_money(sm)
    assert u.strength == 1.0


def test_wallet_score_clamped_below_0():
    sm = make_sm_signal(wallet_score=-0.1)
    u = from_smart_money(sm)
    assert u.strength == 0.0


def test_details_includes_full_audit_payload():
    sm = make_sm_signal(sig_type=SignalType.OPEN_LONG, size_delta=0.3, new_size=0.3, px=48_000)
    u = from_smart_money(sm)
    d = u.details
    assert d["signal_type"] == "open_long"
    assert d["size_delta"] == 0.3
    assert d["new_size"] == 0.3
    assert d["px"] == 48_000.0
    assert d["hl_trade_id"] == 42
    assert "wallet_id" in d
    assert "latency_ms" in d
    assert d["ts_hl_fill_ms"] == 1_700_000_000_000


def test_reason_is_human_readable():
    sm = make_sm_signal(sig_type=SignalType.OPEN_LONG, size_delta=0.5)
    u = from_smart_money(sm)
    assert "whale" in u.reason
    assert "open_long" in u.reason
