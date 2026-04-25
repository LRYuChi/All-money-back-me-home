"""Tests for weekly_review CLI (P2-10, round 47)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategies.cli.weekly_review import (
    _build_parser,
    _delta_arrow,
    _fmt_delta_pct,
    _fmt_pf,
    _format_review,
    _resolve_journal_dir,
    main,
)
from strategies.journal import TradeJournal
from strategies.performance import GroupStats, PerformanceSnapshot


# ================================================================== #
# Helpers
# ================================================================== #
def _seed_exit(j: TradeJournal, ts: str, pnl_pct: float, *,
               pair="BTC/USDT:USDT", side="long",
               reason="trailing_stop", tag="confirmed") -> None:
    j.write({
        "event_type": "exit",
        "timestamp": ts,
        "pair": pair, "side": side,
        "entry_price": 50_000, "exit_price": 50_000 * (1 + pnl_pct/100),
        "pnl_pct": pnl_pct, "pnl_usd": pnl_pct * 5,
        "duration_hours": 4.0, "exit_reason": reason,
        "max_profit_pct": max(pnl_pct, 0),
        "trailing_phase_at_exit": 0, "n_partials_taken": 0,
        "state": {}, "entry_tag": tag,
    })


# ================================================================== #
# Parser
# ================================================================== #
def test_default_window_7_days():
    args = _build_parser().parse_args([])
    assert args.days == 7


def test_custom_days():
    args = _build_parser().parse_args(["--days", "14"])
    assert args.days == 14


def test_dry_run_flag():
    args = _build_parser().parse_args(["--dry-run"])
    assert args.dry_run is True


# ================================================================== #
# _resolve_journal_dir (mirrors daily_summary)
# ================================================================== #
def test_resolve_explicit_arg(tmp_path):
    assert _resolve_journal_dir(tmp_path) == tmp_path


def test_resolve_default_when_unset(monkeypatch):
    monkeypatch.delenv("SUPERTREND_JOURNAL_DIR", raising=False)
    assert str(_resolve_journal_dir(None)) == "trading_log/journal"


# ================================================================== #
# _delta_arrow + _fmt_delta_pct
# ================================================================== #
def test_delta_arrow_higher_better_up():
    assert _delta_arrow(10, 5, higher_better=True) == "📈"


def test_delta_arrow_higher_better_down():
    assert _delta_arrow(5, 10, higher_better=True) == "📉"


def test_delta_arrow_lower_better_drawdown():
    """For drawdown, smaller is better — inverted arrow."""
    assert _delta_arrow(2.0, 5.0, higher_better=False) == "📈"   # less DD = good
    assert _delta_arrow(5.0, 2.0, higher_better=False) == "📉"   # more DD = bad


def test_delta_arrow_no_change():
    assert _delta_arrow(5, 5) == "→"


def test_fmt_delta_pct():
    text = _fmt_delta_pct(60.0, 50.0)
    assert "+10.00" in text
    assert "📈" in text


# ================================================================== #
# _fmt_pf
# ================================================================== #
def test_pf_finite():
    assert _fmt_pf(2.5) == "2.50"


def test_pf_infinite():
    assert _fmt_pf(float("inf")) == "∞"


# ================================================================== #
# _format_review — edge cases
# ================================================================== #
def test_format_no_trades_either_period():
    curr = PerformanceSnapshot()
    prev = PerformanceSnapshot()
    text = _format_review(curr, prev, days=7)
    assert "本週與上週皆無交易" in text


def test_format_includes_core_metrics_when_data_present():
    curr = PerformanceSnapshot(
        n_trades=5, n_wins=3, n_losses=2, win_rate=0.6,
        sum_pnl_usd=15.0, profit_factor=1.5, expectancy_pct=2.0,
        max_drawdown_pct=3.0, kelly_fraction=0.10,
        current_streak=2, longest_win_streak=3, longest_loss_streak=1,
    )
    prev = PerformanceSnapshot(
        n_trades=4, n_wins=2, n_losses=2, win_rate=0.5,
        sum_pnl_usd=5.0, profit_factor=1.2, expectancy_pct=1.0,
        max_drawdown_pct=4.0, kelly_fraction=0.08,
    )
    text = _format_review(curr, prev, days=7)
    # Both numbers present
    assert "5" in text and "4" in text
    assert "60.0%" in text and "50.0%" in text
    assert "1.50" in text and "1.20" in text


# ================================================================== #
# Notable warnings
# ================================================================== #
def test_warns_on_volume_surge():
    """50% jump in trade count → warning."""
    curr = PerformanceSnapshot(n_trades=20, win_rate=0.5)
    prev = PerformanceSnapshot(n_trades=10, win_rate=0.5)
    text = _format_review(curr, prev, days=7)
    assert "交易數激增" in text


def test_warns_when_pf_drops_below_one():
    curr = PerformanceSnapshot(n_trades=10, profit_factor=0.8)
    prev = PerformanceSnapshot(n_trades=10, profit_factor=1.5)
    text = _format_review(curr, prev, days=7)
    assert "獲利因子由" in text


def test_warns_on_streak_near_cb():
    curr = PerformanceSnapshot(n_trades=5, current_streak=-3)
    prev = PerformanceSnapshot(n_trades=5)
    text = _format_review(curr, prev, days=7)
    assert "斷路器閾值" in text


def test_warns_on_dd_explosion():
    """DD jumped > 1.5x previous."""
    curr = PerformanceSnapshot(n_trades=10, max_drawdown_pct=10.0)
    prev = PerformanceSnapshot(n_trades=10, max_drawdown_pct=5.0)
    text = _format_review(curr, prev, days=7)
    assert "DD 擴大" in text


def test_warns_when_kelly_drops_below_5pct():
    curr = PerformanceSnapshot(n_trades=10, kelly_fraction=0.03)
    prev = PerformanceSnapshot(n_trades=10, kelly_fraction=0.10)
    text = _format_review(curr, prev, days=7)
    assert "Kelly 跌至" in text


# ================================================================== #
# Per-pair winners + losers
# ================================================================== #
def test_format_lists_per_pair_winners(tmp_path, capsys):
    """Best 3 pairs in the current window are highlighted."""
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    # 3 pairs, varying performance
    _seed_exit(j, (now - timedelta(days=2)).isoformat(), 5.0, pair="A/USDT:USDT")
    _seed_exit(j, (now - timedelta(days=2)).isoformat(), 3.0, pair="B/USDT:USDT")
    _seed_exit(j, (now - timedelta(days=2)).isoformat(), -2.0, pair="C/USDT:USDT")

    rc = main(["--dry-run", "--dir", str(tmp_path), "--days", "7"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "前 3 名" in out
    assert "倒數 3 名" in out
    assert "A/USDT:USDT" in out


# ================================================================== #
# E2E: dry-run renders without crashing
# ================================================================== #
def test_cli_dry_run_empty_journal(tmp_path, capsys):
    rc = main(["--dry-run", "--dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "週結" in out


def test_cli_dry_run_with_seeded_data(tmp_path, capsys):
    """Two periods of data → comparison rendered."""
    j = TradeJournal(tmp_path)
    now = datetime.now(timezone.utc)
    # Current week (3 wins, 1 loss)
    _seed_exit(j, (now - timedelta(days=1)).isoformat(), 3.0, tag="confirmed")
    _seed_exit(j, (now - timedelta(days=2)).isoformat(), 2.0, tag="scout")
    _seed_exit(j, (now - timedelta(days=3)).isoformat(), -1.0, tag="confirmed")
    # Last week (1 win, 1 loss)
    _seed_exit(j, (now - timedelta(days=10)).isoformat(), 4.0, tag="confirmed")
    _seed_exit(j, (now - timedelta(days=12)).isoformat(), -3.0, tag="scout")

    rc = main(["--dry-run", "--dir", str(tmp_path), "--days", "7"])
    out = capsys.readouterr().out
    assert rc == 0
    # 3 trades current vs 2 prev
    assert "`3`" in out and "`2`" in out
