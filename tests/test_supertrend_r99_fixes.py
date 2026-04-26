"""R99 — two correctness fixes:

1. `leverage()` was mis-indented as a nested function inside the
   module-level `_arrow` helper, so Freqtrade never called it. Verify
   it's now a real class method on SupertrendStrategy.
2. R97 wired guard pipeline CHECKING but not state RECORDING. Without
   record_loss / record_result / record_trade, guards were inert.
   Verify confirm_trade_exit now records guard state.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


# =================================================================== #
# Fix 1: leverage() must be a class method
# =================================================================== #
def test_leverage_is_class_method_not_nested():
    """If leverage is missing from the class, Freqtrade silently uses 1x."""
    assert hasattr(SupertrendStrategy, "leverage"), (
        "SupertrendStrategy.leverage missing — Freqtrade will fall back to "
        "default leverage (1x) instead of quality-weighted 1.5–5x"
    )
    fn = SupertrendStrategy.leverage
    sig = inspect.signature(fn)
    assert "pair" in sig.parameters
    assert "proposed_leverage" in sig.parameters
    assert "max_leverage" in sig.parameters


def test_leverage_returns_quality_weighted_value():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.timeframe = "15m"
    s.dp = MagicMock()
    df = pd.DataFrame([{"trend_quality": 0.8, "adx": 30.0}])
    s.dp.get_analyzed_dataframe.return_value = (df, None)
    from datetime import datetime, timezone
    lev = s.leverage(
        pair="BTC/USDT:USDT",
        current_time=datetime.now(timezone.utc),
        current_rate=50000.0,
        proposed_leverage=1.0,
        max_leverage=10.0,
        entry_tag="scout",
        side="long",
    )
    # quality 0.8 → 1.0 + 0.8*4.0 = 4.2
    # adx 30 → +max(0,0)*0.05 = 0
    # clamp 1.5..5 → 4.2
    assert lev == pytest.approx(4.2)


def test_leverage_clamps_to_min_1_5():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.timeframe = "15m"
    s.dp = MagicMock()
    df = pd.DataFrame([{"trend_quality": 0.0, "adx": 20.0}])  # very weak
    s.dp.get_analyzed_dataframe.return_value = (df, None)
    from datetime import datetime, timezone
    lev = s.leverage(
        pair="X", current_time=datetime.now(timezone.utc),
        current_rate=1.0, proposed_leverage=1.0, max_leverage=10.0,
        entry_tag="scout", side="long",
    )
    # 1.0 + 0*4 = 1.0; ADX adjustment 0; clamp to >= 1.5
    assert lev == pytest.approx(1.5)


def test_leverage_clamps_to_max_5():
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.timeframe = "15m"
    s.dp = MagicMock()
    df = pd.DataFrame([{"trend_quality": 1.0, "adx": 60.0}])
    s.dp.get_analyzed_dataframe.return_value = (df, None)
    from datetime import datetime, timezone
    lev = s.leverage(
        pair="X", current_time=datetime.now(timezone.utc),
        current_rate=1.0, proposed_leverage=1.0, max_leverage=10.0,
        entry_tag="scout", side="long",
    )
    # 1.0 + 1.0*4 = 5.0; ADX 60 → +1.5 → 6.5; clamp to <= 5
    assert lev == pytest.approx(5.0)


# =================================================================== #
# Fix 2: confirm_trade_exit records guard state
# =================================================================== #
def _stub_strategy(monkeypatch):
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.timeframe = "15m"
    s.dp = MagicMock()
    s.dp.get_analyzed_dataframe.return_value = (pd.DataFrame(), None)
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)  # default ON
    return s


def _make_trade(pnl_usd: float):
    """Minimal Trade stub for confirm_trade_exit."""
    t = MagicMock()
    t.calc_profit_ratio.return_value = pnl_usd / 1000.0
    t.calc_profit.return_value = pnl_usd
    t.open_rate = 50000.0
    t.is_short = False
    t.nr_of_successful_exits = 0
    t.get_custom_data.return_value = 0.0
    t.enter_tag = "scout"
    from datetime import datetime, timezone, timedelta
    t.open_date_utc = datetime.now(timezone.utc) - timedelta(hours=1)
    return t


def _call_exit(strategy, pnl_usd: float, balance: float | None = None):
    from datetime import datetime, timezone
    trade = _make_trade(pnl_usd)
    captured_calls: list = []
    fake_cooldown = MagicMock()
    fake_consec = MagicMock()
    fake_daily = MagicMock()
    fake_drawdown = MagicMock()
    if balance is not None:
        strategy.wallets = MagicMock()
        strategy.wallets.get_total.return_value = balance

    def _get_guard(cls):
        from guards.guards import (
            ConsecutiveLossGuard, CooldownGuard, DailyLossGuard, DrawdownGuard,
        )
        return {
            CooldownGuard: fake_cooldown,
            ConsecutiveLossGuard: fake_consec,
            DailyLossGuard: fake_daily,
            DrawdownGuard: fake_drawdown,
        }.get(cls)

    with patch(
        "strategies.supertrend._safe_journal_write",
        side_effect=lambda ev: captured_calls.append(("journal", ev)),
    ), patch(
        "strategies.supertrend._send_to_all_bots",
        side_effect=lambda msg: captured_calls.append(("tg", msg)),
    ), patch(
        "guards.pipeline.get_guard",
        side_effect=_get_guard,
    ), patch(
        "guards.pipeline.save_state",
        side_effect=lambda: captured_calls.append(("save_state", None)),
    ):
        result = strategy.confirm_trade_exit(
            pair="BTC/USDT:USDT", trade=trade, order_type="market",
            amount=0.01, rate=50000.0 + pnl_usd, time_in_force="GTC",
            exit_reason="trailing_stop",
            current_time=datetime.now(timezone.utc),
        )
    return result, fake_cooldown, fake_consec, fake_daily, fake_drawdown, captured_calls


def test_exit_records_cooldown_and_consec_for_winning_trade(monkeypatch):
    s = _stub_strategy(monkeypatch)
    result, cd, cl, dl, _dd, calls = _call_exit(s, pnl_usd=10.0)
    assert result is True
    cd.record_trade.assert_called_once_with("BTC/USDT:USDT")
    cl.record_result.assert_called_once_with(is_win=True)
    dl.record_loss.assert_not_called()   # winners don't update daily-loss
    assert any(c[0] == "save_state" for c in calls)


def test_exit_records_daily_loss_for_losing_trade(monkeypatch):
    s = _stub_strategy(monkeypatch)
    result, cd, cl, dl, _dd, calls = _call_exit(s, pnl_usd=-7.5)
    assert result is True
    cd.record_trade.assert_called_once_with("BTC/USDT:USDT")
    cl.record_result.assert_called_once_with(is_win=False)
    dl.record_loss.assert_called_once_with(7.5)


def test_exit_skips_guard_recording_when_env_disabled(monkeypatch):
    monkeypatch.setenv("SUPERTREND_GUARDS_ENABLED", "0")
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.timeframe = "15m"
    s.dp = MagicMock()
    s.dp.get_analyzed_dataframe.return_value = (pd.DataFrame(), None)
    result, cd, cl, dl, _dd, _calls = _call_exit(s, pnl_usd=-7.5)
    assert result is True
    cd.record_trade.assert_not_called()
    cl.record_result.assert_not_called()
    dl.record_loss.assert_not_called()


# =================================================================== #
# R100: DrawdownGuard.update_equity called on each exit
# =================================================================== #
def test_exit_updates_drawdown_peak_with_current_balance(monkeypatch):
    """R100: DrawdownGuard.update_equity must be called so peak advances
    with profits. Without this, peak is frozen at first observed balance."""
    s = _stub_strategy(monkeypatch)
    _r, _cd, _cl, _dl, dd, _ = _call_exit(s, pnl_usd=10.0, balance=1100.0)
    dd.update_equity.assert_called_once_with(1100.0)


def test_exit_skips_drawdown_update_when_balance_unavailable(monkeypatch):
    """R100: if wallets.get_total raises or returns None, do NOT call
    update_equity with garbage data."""
    s = _stub_strategy(monkeypatch)
    s.wallets = MagicMock()
    s.wallets.get_total.side_effect = RuntimeError("wallet unreachable")
    _r, _cd, _cl, _dl, dd, _ = _call_exit(s, pnl_usd=5.0)
    dd.update_equity.assert_not_called()


def test_exit_skips_drawdown_update_when_balance_zero(monkeypatch):
    """R100: balance=0 is treated as no-data; update_equity NOT called."""
    s = _stub_strategy(monkeypatch)
    _r, _cd, _cl, _dl, dd, _ = _call_exit(s, pnl_usd=5.0, balance=0.0)
    dd.update_equity.assert_not_called()


def test_exit_does_not_fail_when_guards_module_broken(monkeypatch):
    """A broken guards module must not block a legitimate exit."""
    s = _stub_strategy(monkeypatch)
    from datetime import datetime, timezone
    trade = _make_trade(pnl_usd=5.0)
    import sys
    real = sys.modules.get("guards.pipeline")
    sys.modules["guards.pipeline"] = None
    try:
        with patch(
            "strategies.supertrend._safe_journal_write", new=lambda ev: None,
        ), patch(
            "strategies.supertrend._send_to_all_bots", new=lambda m: None,
        ):
            result = s.confirm_trade_exit(
                pair="BTC/USDT:USDT", trade=trade, order_type="market",
                amount=0.01, rate=50005.0, time_in_force="GTC",
                exit_reason="tp", current_time=datetime.now(timezone.utc),
            )
    finally:
        if real is not None:
            sys.modules["guards.pipeline"] = real
        else:
            sys.modules.pop("guards.pipeline", None)
    assert result is True   # exit must always succeed
