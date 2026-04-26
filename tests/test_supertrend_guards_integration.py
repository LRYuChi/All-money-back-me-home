"""R97 — guards wired into SupertrendStrategy.confirm_trade_entry.

CLAUDE.md mandates "every order must pass all guards." Pre-R97, supertrend
(the sole active strategy) had NO guard wiring while legacy strategies
(smc_scalp / bb_squeeze) did. These tests cover:
  - guards default ON; rejection blocks confirm_trade_entry
  - SUPERTREND_GUARDS_ENABLED=0 bypasses entirely (synthetic test mode)
  - guard import failure → fail-open (don't kill the bot)
  - guard.check exception → fail-closed (block, don't permit unsafe entry)
  - rejection writes SkippedEvent + sends Telegram
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


@pytest.fixture
def strategy(monkeypatch):
    """Bare strategy with the dataframe + telegram + journal sides stubbed."""
    s = SupertrendStrategy.__new__(SupertrendStrategy)
    s.adx_threshold = 25.0
    s.timeframe = "15m"
    s._KELLY_LOOKBACK = 30
    # Dataprovider stub returning an empty dataframe
    s.dp = MagicMock()
    s.dp.get_analyzed_dataframe.return_value = (pd.DataFrame(), None)
    s.wallets = MagicMock()
    s.wallets.get_total.return_value = 1000.0
    s.config = {}
    monkeypatch.setenv("SUPERTREND_FR_ALPHA", "0")
    monkeypatch.setenv("SUPERTREND_ORDERBOOK_CONFIRM", "0")
    return s


def _call_confirm(strategy, **overrides):
    """Invoke confirm_trade_entry with sensible defaults.

    Patches journal write + telegram so tests don't touch disk / network.
    Returns (confirm_result, telegram_calls, journal_writes)."""
    from datetime import datetime, timezone
    journal_writes: list = []
    tg_calls: list = []
    with patch(
        "strategies.supertrend._safe_journal_write",
        side_effect=lambda ev: journal_writes.append(ev),
    ), patch(
        "strategies.supertrend._send_to_all_bots",
        side_effect=lambda msg: tg_calls.append(msg),
    ):
        kwargs = {
            "pair": "BTC/USDT:USDT",
            "order_type": "limit",
            "amount": 0.01,
            "rate": 50000.0,
            "time_in_force": "GTC",
            "current_time": datetime.now(timezone.utc),
            "entry_tag": "scout",
            "side": "long",
            "leverage": 1.0,
        }
        kwargs.update(overrides)
        result = strategy.confirm_trade_entry(**kwargs)
    return result, tg_calls, journal_writes


# =================================================================== #
# Default ON — rejection blocks entry, writes journal + telegram
# =================================================================== #
def test_guard_rejection_blocks_entry_and_alerts(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)  # default ON
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = "[L:account] [DailyLossGuard] daily loss exceeded 5%"
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        result, tg, journal = _call_confirm(strategy)
    assert result is False
    # journal got a SkippedEvent with the guard reason
    assert len(journal) == 1
    assert "R97 guard" in journal[0].reason
    assert "DailyLossGuard" in journal[0].reason
    # telegram alert mentions the guard
    assert any("Guard 攔截" in m for m in tg)


def test_guard_pass_allows_entry_path(strategy, monkeypatch):
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = None  # all pass
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        result, _tg, _j = _call_confirm(strategy)
    # Doesn't return False from guard layer — entry path proceeds normally
    # (the call may still return True/False based on later logic, but NOT
    # because of guards).
    fake_pipeline.run.assert_called_once()


# =================================================================== #
# Env opt-out
# =================================================================== #
def test_guards_disabled_bypasses_pipeline_entirely(strategy, monkeypatch):
    monkeypatch.setenv("SUPERTREND_GUARDS_ENABLED", "0")
    fake_pipeline = MagicMock()
    fake_pipeline.run.return_value = "should-not-be-called"
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        _call_confirm(strategy)
    fake_pipeline.run.assert_not_called()


# =================================================================== #
# Fail-safety semantics
# =================================================================== #
def test_guard_module_import_failure_is_fail_open(strategy, monkeypatch):
    """When guards module can't be imported, log warning + permit entry.
    Rationale: a packaging bug in guards/ should NOT take the bot offline."""
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)

    # Patch the import inside _check_guards by monkeypatching sys.modules
    import sys
    real_pipeline_mod = sys.modules.get("guards.pipeline")
    sys.modules["guards.pipeline"] = None   # forces ImportError on `from guards.pipeline ...`
    try:
        result, _tg, journal = _call_confirm(strategy)
    finally:
        if real_pipeline_mod is not None:
            sys.modules["guards.pipeline"] = real_pipeline_mod
        else:
            sys.modules.pop("guards.pipeline", None)

    # Entry is NOT rejected by guards (fails open). Journal has no R97 SkippedEvent.
    assert not any(
        getattr(e, "reason", "").startswith("R97 guard") for e in journal
    )


def test_guard_check_exception_is_fail_closed(strategy, monkeypatch):
    """When pipeline.run() raises, treat as rejection — never silently
    permit potentially unsafe entries."""
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    fake_pipeline = MagicMock()
    fake_pipeline.run.side_effect = RuntimeError("oops")
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        result, _tg, journal = _call_confirm(strategy)
    assert result is False
    assert any(
        getattr(e, "reason", "").startswith("R97 guard")
        and "guard_pipeline_error" in getattr(e, "reason", "")
        for e in journal
    )


# =================================================================== #
# Context construction (R103-corrected)
# =================================================================== #
def test_guard_context_passes_unleveraged_stake(strategy, monkeypatch):
    """R103: GuardContext.amount must be UNLEVERAGED stake. Guards do
    position_value = ctx.amount * ctx.leverage internally, so passing
    notional (already-leveraged) would double-count and silently reject
    legitimate entries once leverage > 1."""
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    fake_pipeline = MagicMock()
    captured_ctx: list = []

    def _capture(ctx):
        captured_ctx.append(ctx)
        return None

    fake_pipeline.run.side_effect = _capture
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        # 0.02 BTC @ $60k @ 5x leverage = $1200 notional, $240 stake
        _call_confirm(strategy, amount=0.02, rate=60000.0, leverage=5.0)
    assert len(captured_ctx) == 1
    ctx = captured_ctx[0]
    # stake = notional / leverage = 1200 / 5 = 240
    assert ctx.amount == pytest.approx(240.0)
    assert ctx.leverage == pytest.approx(5.0)
    # The guard's internal position_value = ctx.amount * ctx.leverage
    # would now correctly recover the $1200 notional.
    assert ctx.amount * ctx.leverage == pytest.approx(1200.0)
    assert ctx.symbol == "BTC/USDT:USDT"
    assert ctx.side == "long"


def test_guard_context_at_1x_leverage_unchanged_semantics(strategy, monkeypatch):
    """At 1x leverage, stake == notional, so behaviour matches pre-R103."""
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    fake_pipeline = MagicMock()
    captured_ctx: list = []
    fake_pipeline.run.side_effect = lambda c: (captured_ctx.append(c), None)[1]
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        _call_confirm(strategy, amount=0.01, rate=50000.0, leverage=1.0)
    # 0.01 * 50000 / 1 = 500 stake; position_value = 500 * 1 = 500
    assert captured_ctx[0].amount == pytest.approx(500.0)
    assert captured_ctx[0].amount * captured_ctx[0].leverage == pytest.approx(500.0)


def test_guard_context_handles_zero_leverage_gracefully(strategy, monkeypatch):
    """If leverage somehow comes through as 0 (edge case), don't divide-by-zero."""
    monkeypatch.delenv("SUPERTREND_GUARDS_ENABLED", raising=False)
    fake_pipeline = MagicMock()
    captured_ctx: list = []
    fake_pipeline.run.side_effect = lambda c: (captured_ctx.append(c), None)[1]
    with patch(
        "guards.pipeline.create_default_pipeline",
        return_value=fake_pipeline,
    ):
        # leverage=0 should not crash; fall back to notional
        _call_confirm(strategy, amount=0.01, rate=50000.0, leverage=0.0)
    # Should fall back to notional (= 500) instead of crashing
    assert captured_ctx[0].amount == pytest.approx(500.0)
