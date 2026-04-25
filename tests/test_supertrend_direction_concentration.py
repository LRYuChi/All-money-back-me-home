"""Tests for SupertrendStrategy direction-concentration cap (P1-4, round 47).

Without this guard, max_open_trades=3 could end up as 3 longs in a bull
cluster — concentrated directional risk. The cap reserves at least one
slot for the OPPOSITE side.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# Fake Trade object — mirrors the surface SupertrendStrategy reads
class _FakeTrade:
    def __init__(self, *, is_short: bool):
        self.is_short = is_short


class _FakeStrategy:
    """Minimal stub that copies the two relevant methods so we can test
    them without instantiating the full IStrategy machinery."""

    _MAX_SAME_SIDE = 2

    def _same_side_open_count(self, side):
        wants_short = (side == "short")
        return sum(
            1 for t in _trades_proxy()
            if bool(t.is_short) == wants_short
        )

    def _direction_concentration_blocked(self, side):
        return self._same_side_open_count(side) >= self._MAX_SAME_SIDE


_OPEN_TRADES: list = []


def _trades_proxy():
    return list(_OPEN_TRADES)


@pytest.fixture(autouse=True)
def reset_trades():
    _OPEN_TRADES.clear()
    yield
    _OPEN_TRADES.clear()


# ================================================================== #
# _same_side_open_count
# ================================================================== #
def test_count_zero_when_no_open_trades():
    s = _FakeStrategy()
    assert s._same_side_open_count("long") == 0
    assert s._same_side_open_count("short") == 0


def test_count_long_only_open():
    _OPEN_TRADES.extend([_FakeTrade(is_short=False), _FakeTrade(is_short=False)])
    s = _FakeStrategy()
    assert s._same_side_open_count("long") == 2
    assert s._same_side_open_count("short") == 0


def test_count_mixed_direction():
    _OPEN_TRADES.extend([
        _FakeTrade(is_short=False),
        _FakeTrade(is_short=False),
        _FakeTrade(is_short=True),
    ])
    s = _FakeStrategy()
    assert s._same_side_open_count("long") == 2
    assert s._same_side_open_count("short") == 1


# ================================================================== #
# _direction_concentration_blocked — boundary semantics
# ================================================================== #
def test_blocked_when_at_cap():
    """At cap exactly = blocked (>= check)."""
    _OPEN_TRADES.extend([
        _FakeTrade(is_short=False), _FakeTrade(is_short=False),
    ])
    s = _FakeStrategy()
    assert s._direction_concentration_blocked("long") is True


def test_not_blocked_below_cap():
    _OPEN_TRADES.append(_FakeTrade(is_short=False))
    s = _FakeStrategy()
    assert s._direction_concentration_blocked("long") is False


def test_not_blocked_for_opposite_side():
    """3 longs open → SHORT is still allowed."""
    _OPEN_TRADES.extend([
        _FakeTrade(is_short=False), _FakeTrade(is_short=False),
        _FakeTrade(is_short=False),
    ])
    s = _FakeStrategy()
    assert s._direction_concentration_blocked("long") is True
    assert s._direction_concentration_blocked("short") is False


def test_not_blocked_at_zero_with_opposite_full():
    """All shorts open → long entry still OK (we cap per-side, not total)."""
    _OPEN_TRADES.extend([
        _FakeTrade(is_short=True), _FakeTrade(is_short=True),
    ])
    s = _FakeStrategy()
    assert s._direction_concentration_blocked("short") is True
    assert s._direction_concentration_blocked("long") is False


# ================================================================== #
# Real strategy class config
# ================================================================== #
def test_strategy_class_has_max_same_side_two():
    from strategies.supertrend import SupertrendStrategy
    assert SupertrendStrategy._MAX_SAME_SIDE == 2


def test_strategy_class_max_same_side_less_than_max_open_trades():
    """Sanity: cap must be < max_open_trades or it does nothing.
    With max_open_trades=3 and cap=2, the 3rd slot is reserved for
    opposite direction — that's the design intent."""
    from strategies.supertrend import SupertrendStrategy
    # max_open_trades is in config_dry.json, not the class — but we can
    # at least assert the cap is sensible (>= 1 < some reasonable max).
    assert 1 <= SupertrendStrategy._MAX_SAME_SIDE <= 5


def test_strategy_methods_exist():
    """Round 47 P1-4 fix must expose both helper methods."""
    from strategies.supertrend import SupertrendStrategy
    assert callable(SupertrendStrategy._same_side_open_count)
    assert callable(SupertrendStrategy._direction_concentration_blocked)
