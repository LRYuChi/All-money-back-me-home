"""Tests for config/freqtrade/config_dry.json — invariants that must hold
or production trading silently breaks.

Live regression source: 2026-04-25 — VPS post-deploy investigation found
freqtrade container Up but bot state=stopped because initial_state was
unset (Freqtrade default = "stopped"). Container would happily report
healthy while never scanning a single candle.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

CONFIG_PATH = (
    Path(__file__).resolve().parent.parent
    / "config" / "freqtrade" / "config_dry.json"
)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return json.loads(CONFIG_PATH.read_text())


# =================================================================== #
# R60: bot must auto-start
# =================================================================== #
def test_initial_state_is_running(cfg):
    """Without initial_state=running, container Up → bot stopped → no trades.
    See R60 — VPS post-mortem 2026-04-25."""
    assert cfg.get("initial_state") == "running", (
        "initial_state must be 'running' or freqtrade boots into "
        "stopped state and silently fails to trade."
    )


# =================================================================== #
# Sanity invariants
# =================================================================== #
def test_dry_run_default_true(cfg):
    """config_dry.json must default to dry_run=True. Live trading is
    opted into via FREQTRADE__DRY_RUN env override (SUPERTREND_LIVE=1)."""
    assert cfg.get("dry_run") is True, (
        "config_dry.json must default to dry_run=True; live mode is "
        "env-override only (SUPERTREND_LIVE=1)."
    )


def test_max_open_trades_matches_concentration_cap(cfg):
    """SupertrendStrategy._MAX_SAME_SIDE = 2 reserves a slot for the
    opposite direction. max_open_trades must be > 2 or the cap is
    pointless."""
    assert cfg.get("max_open_trades", 0) >= 3, (
        "max_open_trades must be >= 3 to leave room for the "
        "_MAX_SAME_SIDE=2 opposite-direction reservation."
    )


def test_trading_mode_is_futures(cfg):
    """OKX perpetuals — spot mode would change exit/leverage semantics
    silently."""
    assert cfg.get("trading_mode") == "futures"


def test_margin_mode_isolated(cfg):
    """Cross margin would let one losing position cascade-liquidate
    the others. Must be isolated."""
    assert cfg.get("margin_mode") == "isolated"


def test_stoploss_on_exchange_enabled(cfg):
    """Without stoploss_on_exchange, a network blip during entry → no
    server-side SL → unbounded downside."""
    assert cfg.get("order_types", {}).get("stoploss_on_exchange") is True


# =================================================================== #
# R63: VolumePairList must be OKX-compatible (candles mode)
# =================================================================== #
def test_volumepairlist_uses_candles_mode_for_okx(cfg):
    """OKX does not support VolumePairList in default ticker mode.
    Without lookback_days + lookback_timeframe set, freqtrade startup
    errors out, pairlists attribute is never created, REST /whitelist
    returns 500, and ZERO pairs get scanned. Regression source:
    2026-04-25 VPS post-mortem — bot up 30+min, 0 trades, root cause
    was a silent VolumePairList init failure."""
    pairlists = cfg.get("pairlists", [])
    vol = next(
        (p for p in pairlists if p.get("method") == "VolumePairList"),
        None,
    )
    if vol is None:
        pytest.skip("VolumePairList not configured — switched to static")
    assert vol.get("lookback_days") is not None, (
        "VolumePairList on OKX MUST set lookback_days to enable "
        "candles-based mode (R63 — see comment in this test)."
    )
    assert vol.get("lookback_timeframe") is not None, (
        "VolumePairList on OKX MUST set lookback_timeframe alongside "
        "lookback_days to enable candles-based mode."
    )
    # Freqtrade rejects refresh_period < one timeframe span. With 1d
    # lookback that's 86400s. Anything smaller and bot startup ERRORs
    # before pairlists init → same zero-scan failure mode.
    tf = vol.get("lookback_timeframe", "1d")
    tf_seconds = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
        "4h": 14400, "1d": 86400, "1w": 604800,
    }.get(tf, 86400)
    rp = vol.get("refresh_period", 0)
    assert rp >= tf_seconds, (
        f"VolumePairList refresh_period={rp}s must be >= one "
        f"lookback_timeframe ({tf} = {tf_seconds}s) or freqtrade "
        f"refuses to start the pairlist plugin."
    )
