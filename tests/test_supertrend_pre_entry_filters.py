"""Tests for R57 — pre-entry alpha filter wiring in SupertrendStrategy.

Tests the standalone helper methods (_funding_filter_block,
_orderbook_filter_block, _pre_entry_filter_block) since exercising the
full confirm_trade_entry would require Freqtrade's DataProvider scaffold.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from strategies.journal import MultiTfState
from strategies.supertrend import SupertrendStrategy


@pytest.fixture
def strategy():
    """A bare strategy instance — only the filter methods exercised."""
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.dp = MagicMock()
    return s


# =================================================================== #
# Funding filter — escape hatch off
# =================================================================== #
def test_fr_filter_off_by_default(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_FR_ALPHA", raising=False)
    # Even an extreme contra-FR is ignored when env unset
    assert strategy._funding_filter_block("long", 0.002) is None
    assert strategy._funding_filter_block("short", -0.002) is None


def test_fr_filter_off_explicit_zero(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "0")
    assert strategy._funding_filter_block("long", 0.002) is None


# =================================================================== #
# Funding filter — enabled, blocking conditions
# =================================================================== #
def test_fr_filter_blocks_long_on_extreme_positive_fr(strategy, monkeypatch):
    """+0.1%/8h fr → strength ≈ -0.76 → blocks long entry."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    reason = strategy._funding_filter_block("long", 0.001)
    assert reason is not None
    assert "long" in reason
    assert "FR" in reason


def test_fr_filter_blocks_short_on_extreme_negative_fr(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    reason = strategy._funding_filter_block("short", -0.001)
    assert reason is not None
    assert "short" in reason


def test_fr_filter_allows_long_on_negative_fr(strategy, monkeypatch):
    """Negative FR favors LONG — no block."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    assert strategy._funding_filter_block("long", -0.001) is None


def test_fr_filter_allows_short_on_positive_fr(strategy, monkeypatch):
    """Positive FR favors SHORT — no block."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    assert strategy._funding_filter_block("short", 0.001) is None


def test_fr_filter_allows_neutral_fr(strategy, monkeypatch):
    """Tiny FR → near-zero strength → no block either side."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    assert strategy._funding_filter_block("long", 0.00005) is None
    assert strategy._funding_filter_block("short", 0.00005) is None


def test_fr_filter_below_block_threshold(strategy, monkeypatch):
    """FR mild against side but below 0.5 strength threshold → no block."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    # fr=+0.0003 → strength tanh(0.3) * -1 ≈ -0.29 → below 0.5
    reason = strategy._funding_filter_block("long", 0.0003)
    assert reason is None


# =================================================================== #
# Orderbook filter — escape hatch
# =================================================================== #
def test_orderbook_filter_off_by_default(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_ORDERBOOK_CONFIRM", raising=False)
    # dp.orderbook is a MagicMock; would return bogus dict, but env off → skip
    assert strategy._orderbook_filter_block("BTC/USDT:USDT", "long") is None
    strategy.dp.orderbook.assert_not_called()


def test_orderbook_filter_blocks_long_on_heavy_ask(strategy, monkeypatch):
    """5-level ask 100x heavier than bid → composite ≈ -0.6 → blocks long."""
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {
        "bids": [[100.0, 1.0]] * 5,
        "asks": [[101.0, 100.0]] * 5,
    }
    reason = strategy._orderbook_filter_block("BTC/USDT:USDT", "long")
    assert reason is not None
    assert "long" in reason


def test_orderbook_filter_allows_long_on_heavy_bid(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {
        "bids": [[100.0, 100.0]] * 5,
        "asks": [[101.0, 1.0]] * 5,
    }
    assert strategy._orderbook_filter_block("BTC/USDT:USDT", "long") is None


def test_orderbook_filter_allows_neutral_book(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {
        "bids": [[100.0, 10.0]] * 5,
        "asks": [[101.0, 10.0]] * 5,
    }
    assert strategy._orderbook_filter_block("BTC/USDT:USDT", "long") is None


def test_orderbook_filter_silent_on_dp_failure(strategy, monkeypatch):
    """REST failure must NOT block trading (defensive)."""
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.side_effect = RuntimeError("connection refused")
    assert strategy._orderbook_filter_block("BTC/USDT:USDT", "long") is None


def test_orderbook_filter_silent_on_empty_book(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {}
    # Empty book → composite 0.0 → neutral → no block
    assert strategy._orderbook_filter_block("BTC/USDT:USDT", "long") is None


# =================================================================== #
# Composite _pre_entry_filter_block
# =================================================================== #
def test_composite_passes_when_all_filters_off(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_FR_ALPHA", raising=False)
    monkeypatch.delenv("SUPERTREND_ORDERBOOK_CONFIRM", raising=False)
    state = MultiTfState(funding_rate=0.002)   # extreme but FR filter off
    assert strategy._pre_entry_filter_block("BTC", "long", state) is None


def test_composite_blocks_on_fr_first(strategy, monkeypatch):
    """FR check runs first — orderbook MUST NOT be queried when FR blocks."""
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    state = MultiTfState(funding_rate=0.002)   # extreme +FR opposing long
    reason = strategy._pre_entry_filter_block("BTC", "long", state)
    assert reason is not None
    assert "FR" in reason
    strategy.dp.orderbook.assert_not_called()


def test_composite_passes_to_orderbook_when_fr_clean(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {
        "bids": [[100.0, 10.0]] * 5,
        "asks": [[101.0, 10.0]] * 5,
    }
    state = MultiTfState(funding_rate=0.0)   # neutral FR
    assert strategy._pre_entry_filter_block("BTC", "long", state) is None
    strategy.dp.orderbook.assert_called_once()


def test_composite_orderbook_blocks_after_fr_pass(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "1")
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "1")
    strategy.dp.orderbook.return_value = {
        "bids": [[100.0, 1.0]] * 5,
        "asks": [[101.0, 100.0]] * 5,   # heavy ask, blocks long
    }
    state = MultiTfState(funding_rate=0.0)
    reason = strategy._pre_entry_filter_block("BTC", "long", state)
    assert reason is not None
    assert "orderbook" in reason
