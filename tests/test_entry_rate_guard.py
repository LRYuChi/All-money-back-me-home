"""Tests for EntryRateGuard — wall-clock circuit breaker against entry bursts.

Background: 2026-04-26 burst incident produced 93 entries in 1 hour while
freqtrade's max_open_trades=3 was respected at every instant (positions
cycled fast). This guard caps entries-per-hour at the strategy layer.
"""

import os

import pytest

from guards.base import GuardContext
from guards.guards import EntryRateGuard


def _ctx() -> GuardContext:
    return GuardContext(
        symbol="BTC/USDT:USDT",
        side="long",
        amount=10.0,
        leverage=1.0,
        account_balance=1000.0,
    )


def test_first_entries_within_cap_pass():
    """The first max_per_hour entries are accepted; check + record alternate."""
    g = EntryRateGuard(max_per_hour=5)
    ctx = _ctx()
    for _ in range(5):
        assert g.check(ctx) is None
        g.record_entry()


def test_overflow_entry_blocked_within_window():
    """The (max_per_hour+1)-th entry within the rolling window is rejected."""
    g = EntryRateGuard(max_per_hour=5)
    ctx = _ctx()
    for _ in range(5):
        assert g.check(ctx) is None
        g.record_entry()
    reason = g.check(ctx)
    assert reason is not None
    assert "EntryRateGuard" in reason
    assert "5 entries in last" in reason
    assert "cap=5" in reason


def test_window_slides_releases_block(monkeypatch):
    """After window_s elapses, old entries are pruned and new ones can pass."""
    g = EntryRateGuard(max_per_hour=5, window_s=3600)
    ctx = _ctx()

    fake_time = [1_000_000.0]
    monkeypatch.setattr("guards.guards.time.time", lambda: fake_time[0])

    for _ in range(5):
        assert g.check(ctx) is None
        g.record_entry()
    assert g.check(ctx) is not None  # 6th blocked

    # Slide window forward past 3600s
    fake_time[0] = 1_000_000.0 + 3700  # 61.7 min later
    assert g.check(ctx) is None  # all 5 pruned, slot freed


def test_partial_window_slide_only_prunes_old_entries(monkeypatch):
    """Sliding mid-window prunes only the entries that fell off the back."""
    g = EntryRateGuard(max_per_hour=5, window_s=3600)
    ctx = _ctx()

    fake_time = [2_000_000.0]
    monkeypatch.setattr("guards.guards.time.time", lambda: fake_time[0])

    # Three entries at t=0
    for _ in range(3):
        g.check(ctx)
        g.record_entry()

    # Two more entries at t=1800 (30 min later)
    fake_time[0] = 2_000_000.0 + 1800
    for _ in range(2):
        g.check(ctx)
        g.record_entry()

    assert g.check(ctx) is not None  # 6th in 30-min window blocked

    # Slide to t=3700: first 3 entries (which were at t=0) pruned, 2 remain
    fake_time[0] = 2_000_000.0 + 3700
    assert g.check(ctx) is None  # 2 in window, 3 free slots


def test_env_var_overrides_default():
    """SUPERTREND_MAX_ENTRIES_PER_HOUR env var overrides constructor default."""
    os.environ["SUPERTREND_MAX_ENTRIES_PER_HOUR"] = "2"
    try:
        g = EntryRateGuard()  # default would be 5; env should force 2
        assert g.max_per_hour == 2
        ctx = _ctx()
        g.check(ctx); g.record_entry()
        g.check(ctx); g.record_entry()
        reason = g.check(ctx)  # 3rd blocked
        assert reason is not None
        assert "cap=2" in reason
    finally:
        del os.environ["SUPERTREND_MAX_ENTRIES_PER_HOUR"]


def test_env_var_invalid_falls_back_to_default():
    """An empty env var must not crash; constructor default applies."""
    os.environ["SUPERTREND_MAX_ENTRIES_PER_HOUR"] = ""
    try:
        g = EntryRateGuard(max_per_hour=7)
        assert g.max_per_hour == 7  # empty string is falsy → default kept
    finally:
        del os.environ["SUPERTREND_MAX_ENTRIES_PER_HOUR"]


def test_record_without_check_still_counts():
    """record_entry adds to window even without a preceding check call."""
    g = EntryRateGuard(max_per_hour=3)
    ctx = _ctx()
    for _ in range(3):
        g.record_entry()
    reason = g.check(ctx)
    assert reason is not None
    assert "3 entries" in reason


def test_state_is_per_instance():
    """Two guard instances do not share state."""
    g1 = EntryRateGuard(max_per_hour=2)
    g2 = EntryRateGuard(max_per_hour=2)
    ctx = _ctx()
    g1.record_entry()
    g1.record_entry()
    assert g1.check(ctx) is not None
    assert g2.check(ctx) is None
