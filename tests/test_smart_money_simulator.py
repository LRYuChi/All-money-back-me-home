"""Tests for smart_money.shadow.simulator (P4c)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from smart_money.execution.mapper import SymbolMapper
from smart_money.shadow.simulator import ShadowSimulator, SimulateResult
from smart_money.signals.types import FollowOrder, RawFillEvent, Signal, SignalType
from smart_money.store.db import InMemoryStore


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


@pytest.fixture
def mapper(tmp_path) -> SymbolMapper:
    p = tmp_path / "sym.yaml"
    p.write_text("""
BTC:
  okx: "BTC/USDT:USDT"
  min_notional_usd: 10
ETH:
  okx: "ETH/USDT:USDT"
  min_notional_usd: 10
""")
    return SymbolMapper.load(p)


def make_signal(
    wallet_id: UUID,
    *,
    symbol: str = "BTC",
    sig_type: SignalType = SignalType.OPEN_LONG,
    size_delta: float = 0.5,
    new_size: float = 0.5,
    px: float = 50_000.0,
    ts_hl_fill_ms: int = 1_700_000_000_000,
) -> Signal:
    event = RawFillEvent(
        wallet_address="0x" + str(wallet_id).replace("-", "")[:40],
        symbol_hl=symbol, side_raw="B", direction_raw="Open Long",
        size=size_delta, px=px, fee=1.0,
        hl_trade_id=int(ts_hl_fill_ms) % 10_000_000,
        ts_hl_fill_ms=ts_hl_fill_ms,
        ts_ws_received_ms=ts_hl_fill_ms + 500,
        ts_queue_processed_ms=ts_hl_fill_ms + 550,
        source="ws",
    )
    return Signal(
        wallet_id=wallet_id,
        wallet_address=event.wallet_address,
        wallet_score=0.7,
        symbol_hl=symbol,
        signal_type=sig_type,
        size_delta=size_delta,
        new_size=new_size,
        px=px,
        whale_equity_usd=1_000_000.0,
        whale_position_usd=new_size * px,
        source_event=event,
    )


def make_order(
    signals: list[Signal],
    *,
    action: str = "open",
    side: str = "buy",
    size_coin: float = 0.5,
) -> FollowOrder:
    return FollowOrder(
        symbol_okx="",
        side=side,
        action=action,
        size_coin=size_coin,
        size_notional_usd=size_coin * signals[0].px,
        source_signals=tuple(signals),
        client_order_id="test-cloid",
        created_ts_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


# ================================================================== #
# Open
# ================================================================== #
def test_open_inserts_paper_trade(store, mapper):
    wid = uuid4()
    sig = make_signal(wid, sig_type=SignalType.OPEN_LONG, px=50_000.0)
    sim = ShadowSimulator(store, mapper, signal_mode="independent")

    result = sim.process(make_order([sig], action="open", side="buy", size_coin=0.5))

    assert result.opened_id is not None
    paper = store.list_paper_trades()[0]
    assert paper.symbol == "BTC/USDT:USDT"
    assert paper.side == "long"
    assert paper.entry_price == 50_000.0
    assert paper.size == 0.5
    assert paper.source_wallet_id == wid
    assert paper.signal_mode == "independent"


def test_open_short_side(store, mapper):
    wid = uuid4()
    sig = make_signal(wid, sig_type=SignalType.OPEN_SHORT, size_delta=0.5)
    sim = ShadowSimulator(store, mapper)

    sim.process(make_order([sig], action="open", side="sell", size_coin=0.5))

    paper = store.list_paper_trades()[0]
    assert paper.side == "short"


def test_unknown_symbol_is_skipped(store, mapper):
    wid = uuid4()
    sig = make_signal(wid, symbol="UNSUPPORTED")
    sim = ShadowSimulator(store, mapper)

    result = sim.process(make_order([sig], action="open"))

    assert result.skipped_reason == "unknown_symbol"
    assert result.opened_id is None
    # No paper trade inserted
    assert store.list_paper_trades() == []
    # Skipped signal recorded
    assert len(store._skipped) == 1
    assert store._skipped[0].reason == "unknown_symbol"


def test_below_min_notional_is_skipped(store, mapper):
    wid = uuid4()
    sig = make_signal(wid, px=50_000.0)  # min_notional_usd=10 for BTC
    sim = ShadowSimulator(store, mapper)

    # 0.0001 BTC * 50k = $5 < $10 min
    result = sim.process(make_order([sig], action="open", size_coin=0.0001))

    assert result.skipped_reason == "below_min_size"
    assert store.list_paper_trades() == []


def test_duplicate_open_for_same_wallet_symbol_skipped(store, mapper):
    wid = uuid4()
    sig = make_signal(wid)
    sim = ShadowSimulator(store, mapper)

    sim.process(make_order([sig], action="open"))
    result = sim.process(make_order([sig], action="open"))

    assert result.skipped_reason == "duplicate_open"
    # Still only 1 paper trade
    assert len(store.list_paper_trades()) == 1


# ================================================================== #
# Close
# ================================================================== #
def test_close_stamps_exit_and_pnl(store, mapper):
    wid = uuid4()
    open_sig = make_signal(wid, sig_type=SignalType.OPEN_LONG, px=50_000.0)
    close_sig = make_signal(
        wid, sig_type=SignalType.CLOSE_LONG, px=55_000.0,
        ts_hl_fill_ms=1_700_000_060_000,  # 1 minute later
    )
    sim = ShadowSimulator(store, mapper)

    sim.process(make_order([open_sig], action="open", side="buy", size_coin=0.5))
    result = sim.process(make_order([close_sig], action="close", side="sell", size_coin=0.5))

    assert result.closed_id is not None
    paper = store.list_paper_trades()[0]
    assert paper.exit_price == 55_000.0
    # PnL: (55000-50000)*0.5 = 2500
    assert paper.pnl == pytest.approx(2_500.0)
    assert paper.closed_at is not None
    assert paper.exit_reason == "whale_close"


def test_close_short_pnl_inverted(store, mapper):
    wid = uuid4()
    open_sig = make_signal(
        wid, sig_type=SignalType.OPEN_SHORT, px=50_000.0, size_delta=0.5,
    )
    close_sig = make_signal(
        wid, sig_type=SignalType.CLOSE_SHORT, px=45_000.0, size_delta=0.5,
        ts_hl_fill_ms=1_700_000_060_000,
    )
    sim = ShadowSimulator(store, mapper)

    sim.process(make_order([open_sig], action="open", side="sell", size_coin=0.5))
    sim.process(make_order([close_sig], action="close", side="buy", size_coin=0.5))

    paper = store.list_paper_trades()[0]
    # Short PnL: (50000-45000)*0.5 = 2500 (profit on drop)
    assert paper.pnl == pytest.approx(2_500.0)


def test_close_without_open_is_skipped(store, mapper):
    wid = uuid4()
    close_sig = make_signal(wid, sig_type=SignalType.CLOSE_LONG)
    sim = ShadowSimulator(store, mapper)

    result = sim.process(make_order([close_sig], action="close", side="sell"))
    assert result.skipped_reason == "close_without_open"


def test_reverse_exit_reason(store, mapper):
    wid = uuid4()
    open_sig = make_signal(wid, sig_type=SignalType.OPEN_LONG, px=50_000.0)
    rev_sig = make_signal(
        wid, sig_type=SignalType.REVERSE_TO_SHORT, px=48_000.0,
        size_delta=1.0, new_size=1.0,
        ts_hl_fill_ms=1_700_000_060_000,
    )
    sim = ShadowSimulator(store, mapper)

    sim.process(make_order([open_sig], action="open", side="buy", size_coin=0.5))
    # Reverse is turned into close + open by the aggregator;
    # simulator receives the close leg here.
    sim.process(make_order([rev_sig], action="close", side="sell", size_coin=0.5))

    paper = store.list_paper_trades()[0]
    assert paper.exit_reason == "reverse"


def test_scale_up_not_simulated(store, mapper):
    wid = uuid4()
    sig = make_signal(wid, sig_type=SignalType.SCALE_UP_LONG, size_delta=0.2)
    sim = ShadowSimulator(store, mapper)

    result = sim.process(make_order([sig], action="scale", side="buy", size_coin=0.2))
    assert result.skipped_reason == "scale_not_simulated_in_shadow"


# ================================================================== #
# Aggregated-mode attribution
# ================================================================== #
def test_aggregated_open_stores_all_source_wallets(store, mapper):
    w1, w2 = uuid4(), uuid4()
    s1 = make_signal(w1, sig_type=SignalType.OPEN_LONG)
    s2 = make_signal(w2, sig_type=SignalType.OPEN_LONG)
    sim = ShadowSimulator(store, mapper, signal_mode="aggregated")

    sim.process(make_order([s1, s2], action="open", side="buy", size_coin=1.0))

    paper = store.list_paper_trades()[0]
    assert paper.source_wallet_id == w1  # primary = first signal's wallet
    assert set(paper.source_wallets) == {w1, w2}
    assert paper.signal_mode == "aggregated"
