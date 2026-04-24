"""Tests for shared.signals.types — UniversalSignal/FusedSignal/StrategyIntent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shared.signals.types import (
    HORIZONS,
    Direction,
    FusedSignal,
    SignalSource,
    StrategyIntent,
    UniversalSignal,
    horizon_to_timedelta,
)


# ------------------------------------------------------------------ #
# UniversalSignal basics
# ------------------------------------------------------------------ #
def test_universal_signal_happy_path():
    sig = UniversalSignal(
        source=SignalSource.KRONOS,
        symbol="crypto:OKX:BTC/USDT:USDT",
        horizon="1h",
        direction=Direction.LONG,
        strength=0.72,
        reason="median forecast +1.2%, consistency 85%",
    )
    assert sig.source == SignalSource.KRONOS
    assert sig.direction == Direction.LONG
    assert sig.strength == 0.72


def test_expires_at_auto_calculated_from_horizon():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    sig = UniversalSignal(
        source=SignalSource.KRONOS,
        symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.5, reason="",
        ts=ts,
    )
    assert sig.expires_at == ts + timedelta(hours=1)


def test_expires_at_explicit_override():
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    custom_exp = ts + timedelta(hours=6)
    sig = UniversalSignal(
        source=SignalSource.AI_LLM,
        symbol="X", horizon="1h",
        direction=Direction.SHORT, strength=0.5, reason="",
        ts=ts, expires_at=custom_exp,
    )
    assert sig.expires_at == custom_exp


def test_strength_out_of_range_rejected():
    with pytest.raises(ValueError, match="strength"):
        UniversalSignal(
            source=SignalSource.TA, symbol="X", horizon="1h",
            direction=Direction.LONG, strength=1.5, reason="",
        )
    with pytest.raises(ValueError, match="strength"):
        UniversalSignal(
            source=SignalSource.TA, symbol="X", horizon="1h",
            direction=Direction.LONG, strength=-0.1, reason="",
        )


def test_invalid_horizon_rejected():
    with pytest.raises(ValueError, match="horizon"):
        UniversalSignal(
            source=SignalSource.TA, symbol="X", horizon="30m",  # not in HORIZONS
            direction=Direction.LONG, strength=0.5, reason="",
        )


def test_is_expired_reports_stale():
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    sig = UniversalSignal(
        source=SignalSource.TA, symbol="X", horizon="15m",
        direction=Direction.LONG, strength=0.5, reason="", ts=old_ts,
    )
    # ts=2h ago, horizon=15m → expires at 1h45m ago → expired
    assert sig.is_expired is True


def test_not_expired_when_fresh():
    sig = UniversalSignal(
        source=SignalSource.TA, symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.5, reason="",
    )
    assert sig.is_expired is False


def test_age_seconds_positive():
    past_ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    sig = UniversalSignal(
        source=SignalSource.TA, symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.5, reason="", ts=past_ts,
    )
    assert 29 <= sig.age_seconds <= 35


# ------------------------------------------------------------------ #
# Immutability (slots + frozen)
# ------------------------------------------------------------------ #
def test_universal_signal_is_frozen():
    sig = UniversalSignal(
        source=SignalSource.TA, symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.5, reason="",
    )
    with pytest.raises(Exception):
        sig.strength = 0.9  # type: ignore[misc]


# ------------------------------------------------------------------ #
# Details dict is per-source free-form but preserved
# ------------------------------------------------------------------ #
def test_details_preserved_as_is():
    details = {"p5": -0.01, "p50": 0.005, "p95": 0.02, "sample_count": 30}
    sig = UniversalSignal(
        source=SignalSource.KRONOS, symbol="X", horizon="1h",
        direction=Direction.LONG, strength=0.6, reason="",
        details=details,
    )
    assert sig.details == details


# ------------------------------------------------------------------ #
# Horizon helpers
# ------------------------------------------------------------------ #
def test_horizon_to_timedelta_all_horizons():
    assert horizon_to_timedelta("15m") == timedelta(minutes=15)
    assert horizon_to_timedelta("1h") == timedelta(hours=1)
    assert horizon_to_timedelta("4h") == timedelta(hours=4)
    assert horizon_to_timedelta("1d") == timedelta(days=1)


def test_horizons_tuple_matches_helper():
    # Defensive: if HORIZONS grows, horizon_to_timedelta must grow too
    for h in HORIZONS:
        horizon_to_timedelta(h)  # should not raise


# ------------------------------------------------------------------ #
# FusedSignal
# ------------------------------------------------------------------ #
def test_fused_signal_happy_path():
    fs = FusedSignal(
        symbol="X", horizon="1h",
        direction=Direction.LONG,
        ensemble_score=0.65,
        regime="BULL_TRENDING",
        sources_count=3,
        contributions={"kronos": 0.3, "smart_money": 0.25, "ta": 0.1},
        conflict=False,
    )
    assert fs.ensemble_score == 0.65
    assert fs.sources_count == 3


def test_fused_signal_ensemble_score_bounds():
    with pytest.raises(ValueError, match="ensemble_score"):
        FusedSignal(
            symbol="X", horizon="1h", direction=Direction.LONG,
            ensemble_score=1.1, regime="BULL_TRENDING",
            sources_count=1, contributions={}, conflict=False,
        )


# ------------------------------------------------------------------ #
# StrategyIntent wraps a FusedSignal for audit
# ------------------------------------------------------------------ #
def test_strategy_intent_carries_fused_for_audit():
    fs = FusedSignal(
        symbol="X", horizon="1h", direction=Direction.LONG,
        ensemble_score=0.7, regime="BULL_TRENDING",
        sources_count=2, contributions={"kronos": 0.4, "ta": 0.3}, conflict=False,
    )
    intent = StrategyIntent(
        strategy_id="crypto_btc_v1",
        symbol="X", direction=Direction.LONG,
        target_notional_usd=500.0,
        entry_price_ref=50_000.0,
        stop_loss_pct=0.02,
        take_profit_pct=None,
        source_fused=fs,
    )
    assert intent.source_fused is fs
    assert intent.target_notional_usd == 500.0
