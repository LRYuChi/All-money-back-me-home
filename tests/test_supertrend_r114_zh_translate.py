"""R114 — Guard rejection translation + entry_logic_summary tests.

Two improvements over R97/R110:
  (a) _translate_guard_reason maps guards/guards.py English messages
      to operator-friendly 繁中 strings (CooldownGuard, DailyLossGuard,
      ConsecutiveLossGuard, MaxPositionGuard, etc.)
  (b) _build_entry_logic_summary explains WHY this tier fired this
      candle — replaces "Entry scout long @ 50000.4 | regime=trending"
      with multi-line breakdown of all 4 quality gate conditions +
      tier-specific just-formed / alignment checks.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from strategies.supertrend import SupertrendStrategy


# =================================================================== #
# (a) Guard translation
# =================================================================== #

def test_cooldown_guard_translation_includes_remaining_time():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:trade] [CooldownGuard] BTC/USDT:USDT cooldown: 3599s remaining"
    )
    assert "⏱️ 冷卻中" in out
    assert "BTC" in out
    assert "59分" in out or "60分" in out  # 3599s ≈ 59min59sec
    assert "USDT:USDT" not in out  # short-form pair


def test_daily_loss_translation():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:account] [DailyLossGuard] Daily loss 12.50 reached 5.0% limit (50.00)"
    )
    assert "💸 單日虧損" in out
    assert "12.5" in out
    assert "5" in out
    assert "上限" in out


def test_consecutive_loss_pause_translation():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:account] [ConsecutiveLossGuard] Trading paused for 23.5h after 5 consecutive losses"
    )
    assert "🚨 連敗暫停" in out
    assert "5" in out
    assert "23.5" in out
    assert "暫停" in out


def test_max_position_translation():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:strategy] [MaxPositionGuard] Position value 1200.00 exceeds 30% of account (300.00)"
    )
    assert "💰 單筆倉位" in out
    assert "1200" in out
    assert "30" in out
    assert "300" in out


def test_max_leverage_translation():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:strategy] [MaxLeverageGuard] Leverage 5.0x exceeds 2.5x for $300 account"
    )
    assert "⚡ 槓桿上限" in out
    assert "5" in out and "2.5" in out
    assert "300" in out


def test_drawdown_translation():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:account] [DrawdownGuard] Portfolio drawdown 12.3% exceeds 10.0% limit (peak: 1100.00, current: 965.30)"
    )
    assert "📉 帳戶回撤" in out
    assert "12.3" in out
    assert "10" in out


def test_unknown_guard_falls_through_with_layer_label():
    out = SupertrendStrategy._translate_guard_reason(
        "[L:strategy] [BrandNewGuard] some unknown reason text"
    )
    assert "策略層" in out
    assert "BrandNewGuard" in out
    assert "some unknown reason text" in out


def test_empty_reason_returns_empty():
    assert SupertrendStrategy._translate_guard_reason("") == ""


def test_no_layer_brackets_returns_input_unchanged():
    """If reason doesn't match the [L:x] [Guard] pattern, return as-is."""
    out = SupertrendStrategy._translate_guard_reason("plain text without brackets")
    assert "plain text without brackets" in out


# =================================================================== #
# (b) Entry logic summary
# =================================================================== #

def _row(**kwargs):
    """Make a pandas Series with sensible defaults for all the columns
    populate_entry_trend reads."""
    base = {
        "adx": 30.0, "volume": 100.0, "volume_ma_20": 50.0,
        "atr_rising": True, "trend_quality": 0.7,
        "st_buy": False, "st_sell": False,
        "all_bullish": False, "all_bearish": False,
        "st_trend": 0, "st_1h": 0,
        "pair_bullish_2tf": False, "pair_bearish_2tf": False,
    }
    base.update(kwargs)
    return pd.Series(base)


def test_confirmed_long_summary_lists_all_conditions():
    last = _row(
        st_buy=True, all_bullish=True,
        adx=30.0, volume=120.0, volume_ma_20=100.0,
        atr_rising=True, trend_quality=0.8,
    )
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="confirmed",
        last_row=last, prev_row=None,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    assert "confirmed" in out
    assert "4 時框完全對齊" in out
    assert "st_buy" in out
    assert "all_bullish" in out
    # 4 quality conditions all show
    assert "ADX" in out and "30.0" in out
    assert "vol" in out and "1.20" in out and "MA20" in out
    assert "ATR 上升" in out
    assert "trend_quality" in out and "0.80" in out
    # All ✓ marks because all conditions met
    assert out.count("✓") >= 5


def test_scout_long_summary_explains_just_formed_check():
    last = _row(all_bullish=True, st_trend=-1, adx=30, trend_quality=0.7,
                volume=120.0, volume_ma_20=100.0, atr_rising=True)
    prev = _row(all_bullish=False, st_trend=-1)
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="scout",
        last_row=last, prev_row=prev,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    assert "scout" in out
    assert "剛剛形成" in out
    assert "st_trend = -1" in out
    assert "✓" in out


def test_pre_scout_short_summary_lists_2tf_just_formed():
    last = _row(pair_bearish_2tf=True, st_1h=1, adx=28, trend_quality=0.6,
                volume=110.0, volume_ma_20=100.0, atr_rising=True)
    prev = _row(pair_bearish_2tf=False)
    out = SupertrendStrategy._build_entry_logic_summary(
        side="short", entry_tag="pre_scout",
        last_row=last, prev_row=prev,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    assert "pre_scout" in out
    assert "最早期試單" in out
    assert "2-tf 空對齊剛形成: ✓" in out
    assert "st_1h = 1" in out


def test_summary_marks_failing_quality_with_X():
    last = _row(
        all_bullish=True, st_trend=-1,
        adx=18.0,                   # < 25 fail
        volume=80.0, volume_ma_20=100.0,   # vol_ratio 0.8 < 1.0 fail
        atr_rising=False,           # fail
        trend_quality=0.4,          # < 0.5 fail
    )
    prev = _row(all_bullish=False)
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="scout",
        last_row=last, prev_row=prev,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    # All 4 quality conditions should show ✗
    assert out.count("✗") >= 4


def test_atr_rising_disabled_message_appears_when_env_off():
    last = _row(all_bullish=True, st_trend=-1, atr_rising=False,
                adx=30, trend_quality=0.7,
                volume=120.0, volume_ma_20=100.0)
    prev = _row(all_bullish=False)
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="scout",
        last_row=last, prev_row=prev,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=False,  # disabled
    )
    assert "已停用" in out


def test_summary_handles_none_last_row_gracefully():
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="scout",
        last_row=None, prev_row=None,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    assert "無資料" in out


def test_summary_handles_unknown_entry_tag():
    last = _row()
    out = SupertrendStrategy._build_entry_logic_summary(
        side="long", entry_tag="weirdo_tier",
        last_row=last, prev_row=None,
        env_vol_mult=1.0, env_quality_min=0.5, env_adx_min=25,
        require_atr_rising=True,
    )
    assert "weirdo_tier" in out
    assert "未知 tier" in out
