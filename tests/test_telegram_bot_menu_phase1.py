"""TG Refactor Phase 1 — feature flag isolation of deprecated buttons.

Verifies:
  * ACTIVE_BUTTONS / LEGACY_BUTTONS partition is correct
  * PERSISTENT_MENU honours TELEGRAM_LEGACY_MENU env
  * process_message returns _legacy_disabled_response for LEGACY commands
    when env is OFF, and dispatches normally when env is ON
  * setup_bot_commands surfaces only ACTIVE commands by default
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import patch

import pytest


def _reload_bot(monkeypatch, *, legacy_enabled: bool):
    """Re-import telegram_bot with TELEGRAM_LEGACY_MENU set as requested."""
    monkeypatch.setenv("TELEGRAM_LEGACY_MENU", "1" if legacy_enabled else "0")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")  # avoid empty-id error path
    monkeypatch.setenv("TG_AI_BOT_TOKEN", "test-token")
    sys.modules.pop("market_monitor.telegram_bot", None)
    return importlib.import_module("market_monitor.telegram_bot")


# =================================================================== #
# Partition + naming invariants
# =================================================================== #

def test_active_and_legacy_are_disjoint(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    overlap = set(bot.ACTIVE_BUTTONS) & set(bot.LEGACY_BUTTONS)
    assert overlap == set(), f"button assigned to BOTH active and legacy: {overlap}"


def test_button_map_is_union_of_active_and_legacy(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    assert set(bot.BUTTON_MAP) == set(bot.ACTIVE_BUTTONS) | set(bot.LEGACY_BUTTONS)


def test_legacy_commands_match_legacy_button_handlers(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    assert bot.LEGACY_COMMANDS == frozenset(bot.LEGACY_BUTTONS.values())


def test_active_buttons_cover_supertrend_essentials(monkeypatch):
    """The active menu must surface the post-R97 trading-essential views."""
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    must_have = {"trades", "positions", "guards", "trade_stats", "journal", "overview"}
    active_cmds = set(bot.ACTIVE_BUTTONS.values())
    missing = must_have - active_cmds
    assert not missing, f"trading-essential commands missing from ACTIVE menu: {missing}"


def test_legacy_buttons_cover_deprecated_subsystems(monkeypatch):
    """Sanity: the deprecated CLAUDE.md list (confidence/tw/ml) is in LEGACY."""
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    must_be_legacy = {
        "confidence", "crypto", "regime", "macro",
        "tw_predict", "tw_chips", "tw_tech", "tw_ml", "btc_ml",
    }
    legacy_cmds = set(bot.LEGACY_BUTTONS.values())
    missing = must_be_legacy - legacy_cmds
    assert not missing, f"deprecated commands not in LEGACY menu: {missing}"


# =================================================================== #
# PERSISTENT_MENU keyboard layout
# =================================================================== #

def test_keyboard_excludes_legacy_when_env_off(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    flat = [btn for row in bot.PERSISTENT_MENU["keyboard"] for btn in row]
    for legacy_btn in bot.LEGACY_BUTTONS:
        assert legacy_btn not in flat, (
            f"legacy button {legacy_btn!r} leaked into keyboard with env OFF"
        )


def test_keyboard_includes_legacy_when_env_on(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=True)
    flat = [btn for row in bot.PERSISTENT_MENU["keyboard"] for btn in row]
    for legacy_btn in bot.LEGACY_BUTTONS:
        assert legacy_btn in flat, (
            f"legacy button {legacy_btn!r} missing when env ON"
        )


def test_keyboard_always_includes_active_buttons(monkeypatch):
    for legacy_state in (False, True):
        bot = _reload_bot(monkeypatch, legacy_enabled=legacy_state)
        flat = [btn for row in bot.PERSISTENT_MENU["keyboard"] for btn in row]
        for active_btn in bot.ACTIVE_BUTTONS:
            assert active_btn in flat, (
                f"active button {active_btn!r} missing (legacy={legacy_state})"
            )


# =================================================================== #
# process_message dispatch behaviour
# =================================================================== #

def test_legacy_button_returns_disabled_stub_when_env_off(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    # 信心 maps to 'confidence' which is in LEGACY_COMMANDS
    out = bot.process_message(chat_id=12345, text="🎯 信心")
    assert "已停用" in out
    assert "TELEGRAM_LEGACY_MENU=1" in out
    # Must not have actually called the handler
    assert "confidence" in out  # the stub mentions the command name


def test_legacy_slash_command_returns_disabled_stub_when_env_off(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    out = bot.process_message(chat_id=12345, text="/tw_predict")
    assert "已停用" in out


def test_active_button_dispatches_normally_with_env_off(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    # patch the handler so we don't actually call freqtrade etc
    with patch.dict(bot.COMMANDS, {"trades": lambda: "OK_TRADES"}):
        out = bot.process_message(chat_id=12345, text="💰 交易")
    assert out == "OK_TRADES"


def test_legacy_button_dispatches_when_env_on(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=True)
    with patch.dict(bot.COMMANDS, {"confidence": lambda: "OK_CONF"}):
        out = bot.process_message(chat_id=12345, text="🎯 信心")
    assert out == "OK_CONF"


def test_unknown_slash_command_falls_through_unchanged(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    out = bot.process_message(chat_id=12345, text="/totally_made_up")
    assert "未知指令" in out
    assert "totally_made_up" in out


# =================================================================== #
# setup_bot_commands /-menu registration
# =================================================================== #

def test_setup_bot_commands_excludes_legacy_when_env_off(monkeypatch):
    """The / autocomplete list must NOT show deprecated commands by default."""
    bot = _reload_bot(monkeypatch, legacy_enabled=False)
    captured: list[bytes] = []

    class _FakeReq:
        def __init__(self, *_a, data=None, **_kw):
            captured.append(data)

    with patch.object(bot.urllib.request, "Request", _FakeReq), \
         patch.object(bot.urllib.request, "urlopen", lambda *a, **kw: None):
        bot.setup_bot_commands()
    assert captured, "setup_bot_commands did not POST anything"
    payload = bot.json.loads(captured[0])
    cmds = {c["command"] for c in payload["commands"]}
    # Active essentials present
    assert "trades" in cmds
    assert "guards" in cmds
    # Legacy not registered
    assert "confidence" not in cmds
    assert "tw_predict" not in cmds


def test_setup_bot_commands_includes_legacy_when_env_on(monkeypatch):
    bot = _reload_bot(monkeypatch, legacy_enabled=True)
    captured: list[bytes] = []

    class _FakeReq:
        def __init__(self, *_a, data=None, **_kw):
            captured.append(data)

    with patch.object(bot.urllib.request, "Request", _FakeReq), \
         patch.object(bot.urllib.request, "urlopen", lambda *a, **kw: None):
        bot.setup_bot_commands()
    payload = bot.json.loads(captured[0])
    cmds = {c["command"] for c in payload["commands"]}
    assert "confidence" in cmds
    assert "macro" in cmds
