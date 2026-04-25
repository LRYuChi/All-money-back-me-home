"""Tests for strategies.performance — aggregator + Markdown formatter (round 46)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategies.journal import (
    EntryEvent,
    ExitEvent,
    MultiTfState,
    TradeJournal,
    default_stoploss_plan,
    default_take_profit_plan,
)
from strategies.performance import (
    GroupStats,
    PerformanceAggregator,
    PerformanceSnapshot,
    format_snapshot_md,
)


# ================================================================== #
# Helpers
# ================================================================== #
def _state() -> MultiTfState:
    return MultiTfState()


def _exit(
    ts: str,
    pnl_pct: float,
    pnl_usd: float = None,
    pair: str = "BTC/USDT:USDT",
    side: str = "long",
    reason: str = "trailing_stop",
    tag: str = "confirmed",
    duration: float = 4.0,
) -> dict:
    """Build an exit event row directly (bypass dataclass for test brevity)."""
    return {
        "event_type": "exit", "timestamp": ts, "pair": pair, "side": side,
        "entry_price": 50_000, "exit_price": 50_000 * (1 + pnl_pct / 100),
        "pnl_pct": pnl_pct,
        "pnl_usd": pnl_usd if pnl_usd is not None else pnl_pct * 5,
        "duration_hours": duration, "exit_reason": reason,
        "max_profit_pct": max(pnl_pct, 0), "trailing_phase_at_exit": 0,
        "n_partials_taken": 0, "state": {}, "entry_tag": tag,
    }


def _seed(j: TradeJournal, exits: list[dict]) -> None:
    """Write a list of exit dicts."""
    for e in exits:
        j.write(e)


# ================================================================== #
# Empty / no-trades
# ================================================================== #
def test_snapshot_empty_journal_returns_zero_trades(tmp_path):
    j = TradeJournal(tmp_path)
    snap = PerformanceAggregator(j).snapshot()
    assert snap.n_trades == 0
    assert snap.win_rate == 0.0
    assert snap.profit_factor == 0.0


# ================================================================== #
# Win rate + averages + sums
# ================================================================== #
def test_basic_win_rate_calculation(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0),
        _exit("2026-04-25T02:00:00+00:00", -1.0),
        _exit("2026-04-25T03:00:00+00:00", 3.0),
        _exit("2026-04-25T04:00:00+00:00", 4.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.n_trades == 4
    assert snap.n_wins == 3
    assert snap.n_losses == 1
    assert snap.win_rate == 0.75


def test_avg_win_loss_calculation(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0),
        _exit("2026-04-25T02:00:00+00:00", 4.0),
        _exit("2026-04-25T03:00:00+00:00", -1.0),
        _exit("2026-04-25T04:00:00+00:00", -3.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.avg_win_pct == pytest.approx(3.0)
    assert snap.avg_loss_pct == pytest.approx(2.0)


def test_sum_pnl_usd(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 0, pnl_usd=10),
        _exit("2026-04-25T02:00:00+00:00", 0, pnl_usd=-5),
        _exit("2026-04-25T03:00:00+00:00", 1, pnl_usd=3),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.sum_pnl_usd == 8


# ================================================================== #
# Profit factor + expectancy
# ================================================================== #
def test_profit_factor_basic(tmp_path):
    """gross wins 6, gross losses 2 → PF 3.0."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 4.0),
        _exit("2026-04-25T02:00:00+00:00", 2.0),
        _exit("2026-04-25T03:00:00+00:00", -2.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.profit_factor == pytest.approx(3.0)


def test_profit_factor_infinite_when_no_losses(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 1.0),
        _exit("2026-04-25T02:00:00+00:00", 2.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.profit_factor == float("inf")


def test_expectancy_per_trade(tmp_path):
    """50% wr × +4% avg − 50% × 2% avg = +1%/trade."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 4.0),
        _exit("2026-04-25T02:00:00+00:00", -2.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.expectancy_pct == pytest.approx(1.0)


# ================================================================== #
# Kelly fraction
# ================================================================== #
def test_kelly_fraction_positive_edge(tmp_path):
    """p=0.6 b=2 → Kelly = 0.4."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit(f"2026-04-25T0{i}:00:00+00:00", 2.0 if i < 6 else -1.0)
        for i in range(1, 11)
    ])
    snap = PerformanceAggregator(j).snapshot()
    # win_rate = 5/10 = 0.5, avg_win/avg_loss = 2/1 = 2
    # Kelly = (0.5 * 2 - 0.5) / 2 = 0.25
    assert snap.kelly_fraction == pytest.approx(0.25)


def test_kelly_zero_when_negative_edge(tmp_path):
    """Loser strategy → Kelly clipped to 0."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 1.0),
        _exit("2026-04-25T02:00:00+00:00", -3.0),
        _exit("2026-04-25T03:00:00+00:00", -2.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.kelly_fraction == 0.0


# ================================================================== #
# Drawdown + streak tracking
# ================================================================== #
def test_max_drawdown_peak_to_trough(tmp_path):
    """+5, -2, +1, -4 → peak after t1 = 5, lowest after t4 = 0 → DD = 5."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 5.0),
        _exit("2026-04-25T02:00:00+00:00", -2.0),
        _exit("2026-04-25T03:00:00+00:00", 1.0),
        _exit("2026-04-25T04:00:00+00:00", -4.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    # Peak cumsum = 5 (after t1) → 4 → 3 (after t3, since 4-2+1 = wait)
    # Actually: cum = 5, 3, 4, 0. Peak = 5, lowest after = 0. DD = 5.
    assert snap.max_drawdown_pct == pytest.approx(5.0)


def test_current_streak_positive_for_consecutive_wins(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", -1.0),
        _exit("2026-04-25T02:00:00+00:00", 2.0),
        _exit("2026-04-25T03:00:00+00:00", 3.0),
        _exit("2026-04-25T04:00:00+00:00", 1.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.current_streak == 3


def test_current_streak_negative_for_consecutive_losses(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 1.0),
        _exit("2026-04-25T02:00:00+00:00", -2.0),
        _exit("2026-04-25T03:00:00+00:00", -3.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.current_streak == -2


def test_longest_streaks_tracked(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 1.0),
        _exit("2026-04-25T02:00:00+00:00", 1.0),
        _exit("2026-04-25T03:00:00+00:00", 1.0),    # 3-win streak
        _exit("2026-04-25T04:00:00+00:00", -1.0),
        _exit("2026-04-25T05:00:00+00:00", -1.0),   # 2-loss streak
        _exit("2026-04-25T06:00:00+00:00", 1.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.longest_win_streak == 3
    assert snap.longest_loss_streak == 2


# ================================================================== #
# Group breakdowns
# ================================================================== #
def test_by_tag_breakdown(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0, tag="scout"),
        _exit("2026-04-25T02:00:00+00:00", -1.0, tag="scout"),
        _exit("2026-04-25T03:00:00+00:00", 3.0, tag="confirmed"),
        _exit("2026-04-25T04:00:00+00:00", 4.0, tag="confirmed"),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.by_tag["scout"].n == 2
    assert snap.by_tag["scout"].win_rate == 0.5
    assert snap.by_tag["confirmed"].n == 2
    assert snap.by_tag["confirmed"].win_rate == 1.0


def test_by_pair_breakdown(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0, pair="BTC/USDT:USDT"),
        _exit("2026-04-25T02:00:00+00:00", 3.0, pair="BTC/USDT:USDT"),
        _exit("2026-04-25T03:00:00+00:00", -1.0, pair="ETH/USDT:USDT"),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.by_pair["BTC/USDT:USDT"].sum_pnl_pct == 5.0
    assert snap.by_pair["ETH/USDT:USDT"].sum_pnl_pct == -1.0


def test_by_exit_reason_breakdown(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0, reason="trailing_stop"),
        _exit("2026-04-25T02:00:00+00:00", 3.0, reason="trailing_stop"),
        _exit("2026-04-25T03:00:00+00:00", -1.0, reason="daily_reversal_exit"),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.by_exit_reason["trailing_stop"].n == 2
    assert snap.by_exit_reason["daily_reversal_exit"].n == 1


# ================================================================== #
# Window filtering
# ================================================================== #
def test_window_filter_by_dates(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-23T12:00:00+00:00", 5.0),
        _exit("2026-04-25T12:00:00+00:00", -1.0),
        _exit("2026-04-27T12:00:00+00:00", 2.0),
    ])
    snap = PerformanceAggregator(j).snapshot(
        from_date=datetime(2026, 4, 24, tzinfo=timezone.utc),
        to_date=datetime(2026, 4, 26, tzinfo=timezone.utc),
    )
    assert snap.n_trades == 1   # only the 25th


def test_last_n_trades_filter(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit(f"2026-04-25T0{i}:00:00+00:00", float(i))
        for i in range(1, 8)
    ])
    snap = PerformanceAggregator(j).snapshot(last_n_trades=3)
    assert snap.n_trades == 3
    # Last 3 are 5/6/7 → all wins
    assert snap.n_wins == 3


# ================================================================== #
# Average duration
# ================================================================== #
def test_avg_duration_hours(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 1.0, duration=2.0),
        _exit("2026-04-25T02:00:00+00:00", 1.0, duration=4.0),
        _exit("2026-04-25T03:00:00+00:00", 1.0, duration=6.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.avg_duration_hours == pytest.approx(4.0)


# ================================================================== #
# Sharpe estimate
# ================================================================== #
def test_sharpe_zero_for_single_trade(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [_exit("2026-04-25T01:00:00+00:00", 5.0)])
    snap = PerformanceAggregator(j).snapshot()
    # std requires N>=2; single-trade returns 0
    assert snap.sharpe_estimate == 0.0


def test_sharpe_positive_for_consistent_wins(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit(f"2026-04-25T0{i}:00:00+00:00", 2.0)
        for i in range(1, 6)
    ])
    snap = PerformanceAggregator(j).snapshot()
    # All same return → std = 0 → sharpe stays 0 (no division by zero)
    assert snap.sharpe_estimate == 0.0


def test_sharpe_meaningful_for_mixed_returns(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 3.0),
        _exit("2026-04-25T02:00:00+00:00", 1.0),
        _exit("2026-04-25T03:00:00+00:00", 2.0),
        _exit("2026-04-25T04:00:00+00:00", -1.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.sharpe_estimate != 0.0


# ================================================================== #
# Markdown formatter (smoke)
# ================================================================== #
def test_format_snapshot_md_empty():
    snap = PerformanceSnapshot()
    text = format_snapshot_md(snap)
    assert "No closed trades" in text


def test_format_snapshot_md_includes_key_fields(tmp_path):
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0, tag="scout"),
        _exit("2026-04-25T02:00:00+00:00", -1.0, tag="confirmed"),
        _exit("2026-04-25T03:00:00+00:00", 3.0, reason="trailing_stop"),
    ])
    snap = PerformanceAggregator(j).snapshot()
    text = format_snapshot_md(snap)
    # All major sections present
    assert "績效快照" in text
    assert "勝率" in text
    assert "獲利因子" in text
    assert "最大回撤" in text
    assert "Kelly" in text
    assert "scout" in text
    assert "confirmed" in text
    assert "trailing_stop" in text


def test_format_snapshot_handles_infinite_pf(tmp_path):
    """∞ symbol shouldn't crash formatter."""
    j = TradeJournal(tmp_path)
    _seed(j, [
        _exit("2026-04-25T01:00:00+00:00", 2.0),
        _exit("2026-04-25T02:00:00+00:00", 3.0),
    ])
    snap = PerformanceAggregator(j).snapshot()
    text = format_snapshot_md(snap)
    assert "∞" in text


# ================================================================== #
# Ignores non-exit events
# ================================================================== #
def test_aggregator_ignores_entry_events(tmp_path):
    """Only exit events count toward stats. Entry events for context."""
    j = TradeJournal(tmp_path)
    j.write({
        "event_type": "entry",
        "timestamp": "2026-04-25T01:00:00+00:00",
        "pair": "BTC/USDT:USDT",
        "pnl_pct": 999.0,   # would skew if counted
    })
    _seed(j, [_exit("2026-04-25T02:00:00+00:00", 2.0)])
    snap = PerformanceAggregator(j).snapshot()
    assert snap.n_trades == 1
    assert snap.avg_win_pct == 2.0
