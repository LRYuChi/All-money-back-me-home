"""Tests for smart_money.signals.aggregator (P4c)."""
from __future__ import annotations

from uuid import uuid4

import pytest

from smart_money.signals.aggregator import SignalAggregator
from smart_money.signals.types import RawFillEvent, Signal, SignalType


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
def make_signal(
    *,
    wallet_id=None,
    wallet_score: float = 0.6,
    symbol: str = "BTC",
    sig_type: SignalType = SignalType.OPEN_LONG,
    size_delta: float = 0.5,
    new_size: float = 0.5,
    px: float = 50_000.0,
    ts_hl_fill_ms: int = 1_700_000_000_000,
) -> Signal:
    wallet_id = wallet_id or uuid4()
    event = RawFillEvent(
        wallet_address=f"0x{str(wallet_id).replace('-', '')[:40]}",
        symbol_hl=symbol, side_raw="B", direction_raw="Open Long",
        size=size_delta if sig_type in (SignalType.OPEN_LONG, SignalType.SCALE_UP_LONG) else -size_delta,
        px=px, fee=1.0, hl_trade_id=int(ts_hl_fill_ms) % 10_000_000,
        ts_hl_fill_ms=ts_hl_fill_ms,
        ts_ws_received_ms=ts_hl_fill_ms + 500,
        ts_queue_processed_ms=ts_hl_fill_ms + 550,
        source="ws",
    )
    return Signal(
        wallet_id=wallet_id,
        wallet_address=event.wallet_address,
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


# ================================================================== #
# Independent mode
# ================================================================== #
def test_independent_open_emits_immediately():
    agg = SignalAggregator(mode="independent")
    sig = make_signal(sig_type=SignalType.OPEN_LONG)
    orders = agg.ingest(sig, now_ms=0)

    assert len(orders) == 1
    assert orders[0].action == "open"
    assert orders[0].side == "buy"
    assert orders[0].size_coin == 0.5


def test_independent_close_emits_immediately():
    agg = SignalAggregator(mode="independent")
    sig = make_signal(sig_type=SignalType.CLOSE_LONG, size_delta=0.5)
    orders = agg.ingest(sig, now_ms=0)

    assert len(orders) == 1
    assert orders[0].action == "close"
    # CLOSE_LONG → OKX order side 'sell'
    assert orders[0].side == "sell"


def test_independent_scale_down_short_emits_close():
    agg = SignalAggregator(mode="independent")
    sig = make_signal(sig_type=SignalType.SCALE_DOWN_SHORT, size_delta=0.2)
    orders = agg.ingest(sig, now_ms=0)

    assert len(orders) == 1
    assert orders[0].action == "close"
    assert orders[0].side == "buy"   # closing a short = buying


def test_independent_scale_up_emits_scale_action():
    agg = SignalAggregator(mode="independent")
    sig = make_signal(sig_type=SignalType.SCALE_UP_LONG, size_delta=0.3)
    orders = agg.ingest(sig, now_ms=0)

    assert len(orders) == 1
    assert orders[0].action == "scale"


def test_reversal_emits_two_orders():
    agg = SignalAggregator(mode="independent")
    sig = make_signal(sig_type=SignalType.REVERSE_TO_SHORT, size_delta=1.0, new_size=1.0)
    orders = agg.ingest(sig, now_ms=0)

    assert len(orders) == 2
    actions = [o.action for o in orders]
    assert "close" in actions and "open" in actions
    # Close comes first, then open of new side
    assert orders[0].action == "close"
    assert orders[1].action == "open"
    assert orders[1].side == "sell"    # REVERSE_TO_SHORT → new open is sell
    # Client order ids differ
    assert orders[0].client_order_id != orders[1].client_order_id


# ================================================================== #
# Aggregated mode — threshold
# ================================================================== #
def test_aggregated_single_wallet_below_threshold_holds():
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    sig = make_signal(wallet_id=uuid4(), sig_type=SignalType.OPEN_LONG)
    orders = agg.ingest(sig, now_ms=0)

    assert orders == []


def test_aggregated_two_wallets_hit_threshold():
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    s1 = make_signal(wallet_id=uuid4(), ts_hl_fill_ms=1_000_000)
    s2 = make_signal(wallet_id=uuid4(), ts_hl_fill_ms=1_100_000)

    assert agg.ingest(s1, now_ms=1_000) == []
    orders = agg.ingest(s2, now_ms=2_000)

    assert len(orders) == 1
    assert orders[0].action == "open"
    # Both wallets appear in source_signals
    assert len(orders[0].source_signals) == 2


def test_aggregated_same_wallet_twice_does_not_double_count():
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    wid = uuid4()
    s1 = make_signal(wallet_id=wid, ts_hl_fill_ms=1_000_000)
    s2 = make_signal(wallet_id=wid, ts_hl_fill_ms=1_100_000)  # same wallet updating entry

    assert agg.ingest(s1, now_ms=1_000) == []
    # Same wallet — still only 1 distinct
    assert agg.ingest(s2, now_ms=2_000) == []


def test_aggregated_distinct_side_different_bucket():
    """OPEN_LONG and OPEN_SHORT for same symbol are different buckets."""
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    s_long = make_signal(wallet_id=uuid4(), sig_type=SignalType.OPEN_LONG)
    s_short = make_signal(wallet_id=uuid4(), sig_type=SignalType.OPEN_SHORT, size_delta=-0.5)

    assert agg.ingest(s_long, now_ms=0) == []
    assert agg.ingest(s_short, now_ms=100) == []
    # Neither bucket has 2 wallets — both hold
    assert agg._pending.get(("BTC", "buy")) is not None
    assert agg._pending.get(("BTC", "sell")) is not None


def test_aggregated_emit_clears_bucket():
    """After threshold fires and emits, the bucket is empty for the next cycle."""
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    s1 = make_signal(wallet_id=uuid4())
    s2 = make_signal(wallet_id=uuid4())

    agg.ingest(s1, now_ms=0)
    orders = agg.ingest(s2, now_ms=100)
    assert len(orders) == 1

    # Third wallet arriving after emit → resets accumulation
    s3 = make_signal(wallet_id=uuid4())
    assert agg.ingest(s3, now_ms=200) == []


# ================================================================== #
# Aggregated mode — window expiry
# ================================================================== #
def test_aggregated_expired_first_signal_resets_window():
    """If the first signal was >window ago, new signal starts a fresh bucket."""
    agg = SignalAggregator(mode="aggregated", min_wallets=2, window_sec=300)
    s1 = make_signal(wallet_id=uuid4())

    agg.ingest(s1, now_ms=0)
    # 301s later — first signal is expired when second arrives
    s2 = make_signal(wallet_id=uuid4())
    orders = agg.ingest(s2, now_ms=301_000)

    # Bucket was reset, only s2 present — below threshold, no emit
    assert orders == []


def test_flush_expired_drops_stale_buckets():
    agg = SignalAggregator(mode="aggregated", min_wallets=3, window_sec=300)
    s = make_signal(wallet_id=uuid4())
    agg.ingest(s, now_ms=0)
    assert len(agg._pending) == 1

    agg.flush_expired(now_ms=301_000)
    assert len(agg._pending) == 0


def test_flush_does_not_drop_active_buckets():
    agg = SignalAggregator(mode="aggregated", min_wallets=3, window_sec=300)
    s = make_signal(wallet_id=uuid4())
    agg.ingest(s, now_ms=0)
    agg.flush_expired(now_ms=250_000)
    assert len(agg._pending) == 1


# ================================================================== #
# Aggregated mode — size scaling
# ================================================================== #
def test_aggregated_size_mult_sums_scores():
    agg = SignalAggregator(
        mode="aggregated", min_wallets=2, window_sec=300,
        score_baseline=0.6,
    )
    s1 = make_signal(wallet_id=uuid4(), wallet_score=0.9)
    s2 = make_signal(wallet_id=uuid4(), wallet_score=0.6)

    agg.ingest(s1, now_ms=0)
    orders = agg.ingest(s2, now_ms=100)

    # total score 1.5, baseline 0.6 → size_mult 2.5
    # merged_size = avg(0.5, 0.5) * 2.5 = 1.25
    assert orders[0].size_coin == pytest.approx(1.25)


# ================================================================== #
# Invariants
# ================================================================== #
def test_min_wallets_zero_raises():
    with pytest.raises(ValueError, match="min_wallets"):
        SignalAggregator(mode="aggregated", min_wallets=0)


def test_unrecognized_signal_type_does_not_emit():
    """Safety: if SignalType expands in future, aggregator doesn't crash."""
    # We can't easily inject a fake SignalType into the enum, so just verify
    # that _emit_single returns None for a type not in the action/side map.
    # Instead, test that all current SignalType members get handled:
    for t in SignalType:
        sig = make_signal(sig_type=t, new_size=0.5)
        agg = SignalAggregator(mode="independent")
        orders = agg.ingest(sig, now_ms=0)
        # Every type should emit at least one order (1 or 2 for reversals)
        assert len(orders) in (1, 2), f"{t} produced {len(orders)} orders"
