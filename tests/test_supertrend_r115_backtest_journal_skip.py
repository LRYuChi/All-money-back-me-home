"""R115 — backtest mode skips journal writes by default.

Background: 2026-04-26 first cron iteration of the new monitoring mandate
caught journal pollution: my own R111 git-bisect (8 backtests × 8
trades) wrote 67 ExitEvents into prod journal between 14:10-14:12,
making `recent_trades=201` look like dry-run was actively trading
when in reality the past 24h had `tier_fired_count={0,0,0}`.

Fix: detect freqtrade backtest invocation via sys.argv at module load
time; in backtest mode, _safe_journal_write becomes a no-op unless the
operator explicitly opts in with SUPERTREND_BACKTEST_WRITE_JOURNAL=1.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


def _reload_strategy_with_argv(argv: list[str], env_write: str | None = None, monkeypatch=None):
    """Reimport the strategy module after setting sys.argv + env."""
    if env_write is not None:
        monkeypatch.setenv("SUPERTREND_BACKTEST_WRITE_JOURNAL", env_write)
    else:
        monkeypatch.delenv("SUPERTREND_BACKTEST_WRITE_JOURNAL", raising=False)
    monkeypatch.setattr(sys, "argv", argv)
    if "strategies.supertrend" in sys.modules:
        del sys.modules["strategies.supertrend"]
    return importlib.import_module("strategies.supertrend")


def test_backtest_mode_detected_from_argv(monkeypatch):
    mod = _reload_strategy_with_argv(
        ["freqtrade", "backtesting", "--strategy", "X"], env_write=None, monkeypatch=monkeypatch,
    )
    assert mod._IS_BACKTEST is True


def test_trade_mode_not_detected_as_backtest(monkeypatch):
    mod = _reload_strategy_with_argv(
        ["freqtrade", "trade", "--strategy", "X"], env_write=None, monkeypatch=monkeypatch,
    )
    assert mod._IS_BACKTEST is False


def test_hyperopt_mode_also_detected_as_backtest(monkeypatch):
    """hyperopt also bombards journal with synthetic events — treat it the same."""
    mod = _reload_strategy_with_argv(
        ["freqtrade", "hyperopt", "--strategy", "X"], env_write=None, monkeypatch=monkeypatch,
    )
    assert mod._IS_BACKTEST is True


def test_backtest_mode_skips_journal_write_by_default(monkeypatch):
    mod = _reload_strategy_with_argv(
        ["freqtrade", "backtesting"], env_write=None, monkeypatch=monkeypatch,
    )
    fake_event = MagicMock()
    mock_journal = MagicMock()
    monkeypatch.setattr(mod, "_journal", mock_journal)

    mod._safe_journal_write(fake_event)
    mock_journal.write.assert_not_called()


def test_backtest_mode_writes_journal_when_env_opt_in(monkeypatch):
    mod = _reload_strategy_with_argv(
        ["freqtrade", "backtesting"], env_write="1", monkeypatch=monkeypatch,
    )
    fake_event = MagicMock()
    mock_journal = MagicMock()
    monkeypatch.setattr(mod, "_journal", mock_journal)

    mod._safe_journal_write(fake_event)
    mock_journal.write.assert_called_once_with(fake_event)


def test_trade_mode_writes_journal_normally(monkeypatch):
    """Non-backtest invocations must continue writing journal events."""
    mod = _reload_strategy_with_argv(
        ["freqtrade", "trade"], env_write=None, monkeypatch=monkeypatch,
    )
    fake_event = MagicMock()
    mock_journal = MagicMock()
    monkeypatch.setattr(mod, "_journal", mock_journal)

    mod._safe_journal_write(fake_event)
    mock_journal.write.assert_called_once_with(fake_event)


def test_journal_unavailable_still_skipped_in_backtest(monkeypatch):
    """Sanity: when _journal is None AND we're in backtest, no error."""
    mod = _reload_strategy_with_argv(
        ["freqtrade", "backtesting"], env_write=None, monkeypatch=monkeypatch,
    )
    monkeypatch.setattr(mod, "_journal", None)
    mod._safe_journal_write(MagicMock())   # Should not raise
