"""Tests for strategies.orderbook_signals — R52 microstructure module."""
from __future__ import annotations

import pytest

from strategies.orderbook_signals import (
    ABORT_THRESHOLD,
    CONFIRM_THRESHOLD,
    OrderBookSignal,
    bid_ask_imbalance,
    combine,
    evaluate,
    large_order_pressure,
    should_confirm_entry,
)


# =================================================================== #
# bid_ask_imbalance — book depth analysis
# =================================================================== #
def test_imbalance_balanced_book():
    """Equal bid + ask sizes → 0 imbalance."""
    book = {
        "bids": [[100, 1.0], [99, 1.0], [98, 1.0]],
        "asks": [[101, 1.0], [102, 1.0], [103, 1.0]],
    }
    imb, _, _ = bid_ask_imbalance(book)
    assert imb == 0.0


def test_imbalance_bid_dominant():
    """All size on bids → imbalance close to +1."""
    book = {
        "bids": [[100, 10.0], [99, 10.0]],
        "asks": [[101, 0.1], [102, 0.1]],
    }
    imb, _, _ = bid_ask_imbalance(book)
    assert imb > 0.95


def test_imbalance_ask_dominant():
    book = {
        "bids": [[100, 0.1], [99, 0.1]],
        "asks": [[101, 10.0], [102, 10.0]],
    }
    imb, _, _ = bid_ask_imbalance(book)
    assert imb < -0.95


def test_imbalance_respects_depth_param():
    """Only top N levels counted."""
    book = {
        "bids": [[100, 1.0]] + [[99 - i, 100.0] for i in range(20)],
        "asks": [[101, 1.0]] + [[102 + i, 100.0] for i in range(20)],
    }
    # depth=1 → 1 vs 1 = balanced
    imb_d1, _, _ = bid_ask_imbalance(book, depth=1)
    assert imb_d1 == 0.0
    # depth=20 → tons of size both sides, still balanced
    imb_d20, _, _ = bid_ask_imbalance(book, depth=20)
    assert imb_d20 == 0.0


def test_imbalance_empty_book_returns_zero():
    assert bid_ask_imbalance({}) == (0.0, 0, 0)
    assert bid_ask_imbalance({"bids": [], "asks": [[100, 1]]}) == (0.0, 0, 0)
    assert bid_ask_imbalance({"bids": [[99, 1]], "asks": []}) == (0.0, 0, 0)


def test_imbalance_malformed_book_safe():
    """Defensive: weird input shouldn't crash."""
    assert bid_ask_imbalance(None) == (0.0, 0, 0)
    assert bid_ask_imbalance("not a dict") == (0.0, 0, 0)
    # Malformed levels
    book = {"bids": [["bad", "data"]], "asks": [[100, 1]]}
    imb, _, _ = bid_ask_imbalance(book)
    # Bad bid size becomes 0 → all weight on ask → -1
    assert imb == -1.0


def test_imbalance_in_unit_interval():
    """All inputs produce result in [-1, 1]."""
    for bid_size in [0.0, 0.1, 1.0, 100.0]:
        for ask_size in [0.0, 0.1, 1.0, 100.0]:
            if bid_size == 0 and ask_size == 0:
                continue
            book = {
                "bids": [[100, bid_size]],
                "asks": [[101, ask_size]],
            }
            imb, _, _ = bid_ask_imbalance(book)
            assert -1.0 <= imb <= 1.0


def test_imbalance_returns_actual_level_count():
    book = {
        "bids": [[100, 1], [99, 1], [98, 1]],
        "asks": [[101, 1], [102, 1]],
    }
    _, n_bid, n_ask = bid_ask_imbalance(book, depth=5)
    assert n_bid == 3
    assert n_ask == 2


# =================================================================== #
# large_order_pressure
# =================================================================== #
def test_pressure_no_trades():
    assert large_order_pressure([]) == (0.0, 0)


def test_pressure_only_small_trades():
    """Trades below threshold are ignored."""
    trades = [
        {"side": "buy", "amount": 0.001, "price": 50_000},   # $50 (small)
        {"side": "sell", "amount": 0.001, "price": 50_000},
    ]
    pres, n = large_order_pressure(trades, threshold_usd=50_000)
    assert pres == 0.0
    assert n == 0


def test_pressure_buy_dominant():
    """Big market buys → positive pressure."""
    trades = [
        {"side": "buy", "amount": 2.0, "price": 50_000},   # $100k
        {"side": "buy", "amount": 1.5, "price": 50_000},   # $75k
        {"side": "sell", "amount": 0.001, "price": 50_000}, # $50 (filtered)
    ]
    pres, n = large_order_pressure(trades, threshold_usd=50_000)
    assert pres == 1.0   # all qualifying are buys
    assert n == 2


def test_pressure_sell_dominant():
    trades = [
        {"side": "sell", "amount": 2.0, "price": 50_000},
        {"side": "sell", "amount": 3.0, "price": 50_000},
    ]
    pres, _ = large_order_pressure(trades, threshold_usd=50_000)
    assert pres == -1.0


def test_pressure_balanced():
    trades = [
        {"side": "buy", "amount": 2.0, "price": 50_000},
        {"side": "sell", "amount": 2.0, "price": 50_000},
    ]
    pres, _ = large_order_pressure(trades, threshold_usd=50_000)
    assert pres == 0.0


def test_pressure_max_lookback_caps_trades():
    """Only first N trades counted."""
    trades = [{"side": "buy", "amount": 2.0, "price": 50_000}] * 200
    pres, n = large_order_pressure(
        trades, threshold_usd=50_000, max_lookback=10,
    )
    assert n == 10


def test_pressure_handles_malformed_trades():
    trades = [
        {"side": "buy", "amount": 2.0, "price": 50_000},
        {"side": "buy"},   # missing fields
        "not a dict",
        {"side": "buy", "amount": "bad", "price": 50_000},
    ]
    pres, n = large_order_pressure(trades, threshold_usd=50_000)
    # Only 1 valid trade
    assert n == 1
    assert pres == 1.0


# =================================================================== #
# combine — weighted average
# =================================================================== #
def test_combine_default_60_40_weights():
    """0.5 imbalance + 0 pressure = 0.30 (0.5 × 0.6)."""
    assert combine(0.5, 0.0) == 0.3


def test_combine_both_strong_same_direction():
    assert combine(1.0, 1.0) == pytest.approx(1.0)


def test_combine_opposite_signals_partial_cancel():
    """+0.5 vs -0.5 with 60/40 → 0.5×0.6 + (-0.5)×0.4 = 0.10."""
    assert combine(0.5, -0.5) == pytest.approx(0.10)


def test_combine_custom_weights():
    """Equal weighting."""
    assert combine(0.5, -0.5, imbalance_weight=0.5, pressure_weight=0.5) == 0.0


# =================================================================== #
# evaluate — full signal
# =================================================================== #
def test_evaluate_returns_orderbook_signal_struct():
    book = {"bids": [[100, 1]], "asks": [[101, 1]]}
    trades = []
    sig = evaluate(book, trades)
    assert isinstance(sig, OrderBookSignal)
    assert -1.0 <= sig.composite <= 1.0
    assert sig.n_bid_levels == 1


def test_evaluate_aggregates_correctly():
    book = {
        "bids": [[100, 5.0]] * 5,
        "asks": [[101, 1.0]] * 5,
    }
    trades = [
        {"side": "buy", "amount": 2.0, "price": 50_000},
    ]
    sig = evaluate(book, trades)
    # imbalance positive (more bid), pressure positive (buy taker) → composite positive
    assert sig.composite > 0


# =================================================================== #
# should_confirm_entry — decision logic
# =================================================================== #
def _signal(composite: float) -> OrderBookSignal:
    return OrderBookSignal(
        imbalance=composite, pressure=0,
        composite=composite, n_bid_levels=5, n_ask_levels=5,
        n_recent_trades=10,
    )


def test_proceed_when_book_strongly_supports_long():
    sig = _signal(0.5)   # bid-heavy
    proceed, reason = should_confirm_entry(sig, "long")
    assert proceed is True
    assert "confirms" in reason


def test_proceed_when_book_strongly_supports_short():
    sig = _signal(-0.5)
    proceed, reason = should_confirm_entry(sig, "short")
    assert proceed is True


def test_abort_when_book_strongly_against_long():
    """Composite -0.5 (book ask-heavy) and we want to long → ABORT."""
    sig = _signal(-0.5)
    proceed, reason = should_confirm_entry(sig, "long")
    assert proceed is False
    assert "against" in reason


def test_abort_when_book_strongly_against_short():
    sig = _signal(0.5)
    proceed, reason = should_confirm_entry(sig, "short")
    assert proceed is False


def test_proceed_when_neutral():
    sig = _signal(0.1)   # below CONFIRM_THRESHOLD 0.3
    proceed, _ = should_confirm_entry(sig, "long")
    assert proceed is True


def test_threshold_boundary_exactly_at_abort():
    """At -0.3 in our direction → abort (≤ -ABORT)."""
    sig = _signal(-0.3)
    proceed, _ = should_confirm_entry(sig, "long")
    assert proceed is False


def test_threshold_boundary_just_below_abort():
    sig = _signal(-0.29)
    proceed, _ = should_confirm_entry(sig, "long")
    assert proceed is True


# =================================================================== #
# Threshold constants
# =================================================================== #
def test_thresholds_sane_values():
    assert 0 < CONFIRM_THRESHOLD < 1
    assert -1 < ABORT_THRESHOLD < 0
    # ABORT is opposite-sign of CONFIRM (symmetric design)
    assert CONFIRM_THRESHOLD == -ABORT_THRESHOLD
