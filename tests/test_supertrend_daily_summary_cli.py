"""Tests for daily_summary CLI (P1-5, round 47)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from strategies.cli.daily_summary import (
    _build_parser,
    _format_combined,
    _resolve_journal_dir,
    main,
)


# ================================================================== #
# Parser
# ================================================================== #
def test_default_window_24_hours():
    args = _build_parser().parse_args([])
    assert args.hours == 24
    assert args.days is None


def test_days_overrides_hours():
    args = _build_parser().parse_args(["--days", "7"])
    assert args.days == 7


def test_hours_and_days_mutually_exclusive():
    with pytest.raises(SystemExit):
        _build_parser().parse_args(["--hours", "24", "--days", "7"])


def test_dry_run_flag():
    args = _build_parser().parse_args(["--dry-run"])
    assert args.dry_run is True


def test_include_cumulative_flag():
    args = _build_parser().parse_args(["--include-cumulative"])
    assert args.include_cumulative is True


# ================================================================== #
# _resolve_journal_dir
# ================================================================== #
def test_resolve_explicit_arg_wins(tmp_path):
    p = _resolve_journal_dir(tmp_path)
    assert p == tmp_path


def test_resolve_falls_back_to_env(monkeypatch):
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", "/custom/path")
    p = _resolve_journal_dir(None)
    assert str(p) == "/custom/path"


def test_resolve_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("SUPERTREND_JOURNAL_DIR", raising=False)
    p = _resolve_journal_dir(None)
    assert str(p) == "trading_log/journal"


def test_resolve_env_with_whitespace_falls_back(monkeypatch):
    """Empty/whitespace env var → fall back to default."""
    monkeypatch.setenv("SUPERTREND_JOURNAL_DIR", "   ")
    p = _resolve_journal_dir(None)
    assert str(p) == "trading_log/journal"


# ================================================================== #
# _format_combined
# ================================================================== #
def test_format_window_only():
    text = _format_combined("WINDOW_BODY", None, "近 24h")
    assert "Supertrend 日結 — 近 24h" in text
    assert "WINDOW_BODY" in text
    assert "全期累計" not in text


def test_format_with_cumulative():
    text = _format_combined("WINDOW_BODY", "CUM_BODY", "近 7 天")
    assert "WINDOW_BODY" in text
    assert "CUM_BODY" in text
    assert "全期累計" in text


# ================================================================== #
# CLI dry-run end-to-end
# ================================================================== #
def test_cli_dry_run_with_empty_journal(tmp_path, capsys):
    """Empty journal → "no closed trades" message rendered."""
    rc = main(["--dry-run", "--dir", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Supertrend 日結" in out
    assert "近 24h" in out


def test_cli_dry_run_includes_cumulative_section(tmp_path, capsys):
    rc = main(["--dry-run", "--dir", str(tmp_path), "--include-cumulative"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "全期累計" in out


def test_cli_dry_run_with_seeded_trades(tmp_path, capsys):
    """Seed an exit event then verify it's reflected in the output."""
    from strategies.journal import TradeJournal

    j = TradeJournal(tmp_path)
    # Write an exit event from inside the 24h window
    now = datetime.now(timezone.utc)
    j.write({
        "event_type": "exit",
        "timestamp": (now - timedelta(hours=1)).isoformat(),
        "pair": "BTC/USDT:USDT",
        "side": "long",
        "entry_price": 50_000, "exit_price": 51_000,
        "pnl_pct": 2.0, "pnl_usd": 10.0,
        "duration_hours": 4.0,
        "exit_reason": "trailing_stop",
        "max_profit_pct": 2.5,
        "trailing_phase_at_exit": 1,
        "n_partials_taken": 0,
        "state": {},
        "entry_tag": "confirmed",
    })

    rc = main(["--dry-run", "--dir", str(tmp_path), "--hours", "24"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "交易數" in out
    # Should reflect 1 trade with positive PnL
    assert "勝 `1`" in out or "勝率" in out


def test_cli_window_label_for_short_window(tmp_path, capsys):
    rc = main(["--dry-run", "--dir", str(tmp_path), "--hours", "12"])
    out = capsys.readouterr().out
    assert "近 12h" in out


def test_cli_window_label_for_multi_day(tmp_path, capsys):
    rc = main(["--dry-run", "--dir", str(tmp_path), "--hours", "168"])
    out = capsys.readouterr().out
    # 168h = 7 days → label uses days
    assert "近 7 天" in out


def test_cli_days_label(tmp_path, capsys):
    rc = main(["--dry-run", "--dir", str(tmp_path), "--days", "30"])
    out = capsys.readouterr().out
    assert "近 30 天" in out
