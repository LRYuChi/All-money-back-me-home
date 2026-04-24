"""Tests for strategy_engine.runtime — StrategyRuntime."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fusion import Regime, SignalFuser, load_weights, DEFAULT_WEIGHTS_PATH
from shared.signals.types import (
    Direction,
    SignalSource,
    StrategyIntent,
    UniversalSignal,
)
from strategy_engine import (
    InMemoryStrategyRegistry,
    StrategyRuntime,
    load_strategy,
)


DEFAULT_STRATEGY = Path("config/strategies/crypto_btc_smart_money_v1.yaml")


# ================================================================== #
# Helpers
# ================================================================== #
def make_signal(
    source=SignalSource.SMART_MONEY,
    direction=Direction.LONG,
    strength=0.85,
    symbol="crypto:hyperliquid:BTC",
    horizon="15m",
    expires_in_min=30,
    ts=None,
) -> UniversalSignal:
    ts = ts or datetime.now(timezone.utc)
    return UniversalSignal(
        source=source, symbol=symbol, horizon=horizon,
        direction=direction, strength=strength, reason="test",
        ts=ts,
        expires_at=ts + timedelta(minutes=expires_in_min) if expires_in_min else None,
    )


def build_runtime(
    *,
    regime=Regime.BULL_TRENDING,
    capital=10_000.0,
    on_intent=None,
    strategy_yaml_text: str | None = None,
):
    weights = load_weights(DEFAULT_WEIGHTS_PATH)
    fuser = SignalFuser(weights)
    registry = InMemoryStrategyRegistry()
    if strategy_yaml_text:
        registry.upsert(strategy_yaml_text)
    else:
        registry.upsert(DEFAULT_STRATEGY.read_text())
    return StrategyRuntime(
        registry=registry,
        fuser=fuser,
        regime_provider=lambda: regime,
        capital_provider=lambda: capital,
        on_intent=on_intent,
    )


# ================================================================== #
# Ingest
# ================================================================== #
def test_ingest_increments_buffer():
    rt = build_runtime()
    rt.ingest(make_signal())
    rt.ingest(make_signal(source=SignalSource.KRONOS))
    assert rt.buffer_size("crypto:hyperliquid:BTC", "15m") == 2


def test_ingest_separates_by_symbol_and_horizon():
    rt = build_runtime()
    rt.ingest(make_signal(symbol="crypto:hyperliquid:BTC", horizon="15m"))
    rt.ingest(make_signal(symbol="crypto:hyperliquid:ETH", horizon="15m"))
    rt.ingest(make_signal(symbol="crypto:hyperliquid:BTC", horizon="1h"))
    assert rt.buffer_size("crypto:hyperliquid:BTC", "15m") == 1
    assert rt.buffer_size("crypto:hyperliquid:ETH", "15m") == 1
    assert rt.buffer_size("crypto:hyperliquid:BTC", "1h") == 1


def test_ingest_drops_already_expired():
    """Expired signal added → trimmed immediately on next ingest."""
    rt = build_runtime()
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    rt.ingest(make_signal(ts=old_ts, expires_in_min=30))  # expired by 1.5h
    # Second ingest triggers trim of expired
    rt.ingest(make_signal())
    assert rt.buffer_size("crypto:hyperliquid:BTC", "15m") == 1


# ================================================================== #
# evaluate_all — happy path
# ================================================================== #
def test_evaluate_no_signals_returns_empty():
    rt = build_runtime()
    intents = rt.evaluate_all()
    assert intents == []


def test_evaluate_fires_intent_when_strategy_matches():
    fired = []
    rt = build_runtime(on_intent=lambda i: fired.append(i))
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY, strength=0.85))
    rt.ingest(make_signal(source=SignalSource.KRONOS, strength=0.7))

    intents = rt.evaluate_all()
    assert len(intents) == 1
    assert intents[0].strategy_id == "crypto_btc_smart_money_v1"
    assert intents[0].direction == Direction.LONG
    assert len(fired) == 1


def test_evaluate_skips_when_only_one_source():
    """Strategy requires sources_count >= 2."""
    rt = build_runtime()
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY, strength=0.95))
    intents = rt.evaluate_all()
    assert intents == []


def test_evaluate_skips_unmatched_symbol():
    """Strategy is for BTC; ETH signals should not fire it."""
    rt = build_runtime()
    rt.ingest(make_signal(symbol="crypto:hyperliquid:ETH",
                          source=SignalSource.SMART_MONEY, strength=0.9))
    rt.ingest(make_signal(symbol="crypto:hyperliquid:ETH",
                          source=SignalSource.KRONOS, strength=0.8))
    intents = rt.evaluate_all()
    assert intents == []


def test_evaluate_skips_unmatched_horizon():
    """Strategy is 15m; 1h signals should not fire it."""
    rt = build_runtime()
    rt.ingest(make_signal(horizon="1h", source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(horizon="1h", source=SignalSource.KRONOS))
    intents = rt.evaluate_all()
    assert intents == []


def test_evaluate_blocked_by_crisis_regime():
    """CRISIS regime + strong long signals → strategy `none_of` blocks."""
    rt = build_runtime(regime=Regime.CRISIS)
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY, strength=0.95))
    rt.ingest(make_signal(source=SignalSource.KRONOS, strength=0.9))

    intents = rt.evaluate_all()
    assert intents == []


# ================================================================== #
# Multi-strategy
# ================================================================== #
def test_multiple_strategies_match_independently():
    """Two strategies for different symbols → only the matching one fires."""
    weights = load_weights(DEFAULT_WEIGHTS_PATH)
    registry = InMemoryStrategyRegistry()

    btc_yaml = DEFAULT_STRATEGY.read_text()
    eth_yaml = btc_yaml.replace(
        "id: crypto_btc_smart_money_v1", "id: crypto_eth_smart_money_v1"
    ).replace(
        "crypto:hyperliquid:BTC", "crypto:hyperliquid:ETH"
    ).replace(
        "Follow whitelisted whales on BTC", "Follow whitelisted whales on ETH"
    )
    registry.upsert(btc_yaml)
    registry.upsert(eth_yaml)

    rt = StrategyRuntime(
        registry=registry,
        fuser=SignalFuser(weights),
        regime_provider=lambda: Regime.BULL_TRENDING,
        capital_provider=lambda: 10_000,
    )

    # Only BTC signals
    rt.ingest(make_signal(symbol="crypto:hyperliquid:BTC", source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(symbol="crypto:hyperliquid:BTC", source=SignalSource.KRONOS))

    intents = rt.evaluate_all()
    assert len(intents) == 1
    assert intents[0].strategy_id == "crypto_btc_smart_money_v1"


def test_disabled_strategy_skipped():
    """list_active filters out disabled — no intents from disabled strategies."""
    weights = load_weights(DEFAULT_WEIGHTS_PATH)
    registry = InMemoryStrategyRegistry()
    yaml_text = DEFAULT_STRATEGY.read_text().replace("enabled: true", "enabled: false")
    registry.upsert(yaml_text)

    rt = StrategyRuntime(
        registry=registry,
        fuser=SignalFuser(weights),
        regime_provider=lambda: Regime.BULL_TRENDING,
        capital_provider=lambda: 10_000,
    )
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(source=SignalSource.KRONOS))
    assert rt.evaluate_all() == []


# ================================================================== #
# Callback resilience
# ================================================================== #
def test_intent_callback_exception_does_not_break_loop():
    """A failing on_intent callback must not abort the eval loop."""
    def bad_callback(intent):
        raise RuntimeError("DB write failed")

    rt = build_runtime(on_intent=bad_callback)
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(source=SignalSource.KRONOS))

    intents = rt.evaluate_all()
    # Intent still produced and returned
    assert len(intents) == 1
    # Stats record the error
    assert rt.stats()["intent_callback_errors"] == 1
    assert rt.stats()["intents_fired"] == 1


# ================================================================== #
# Stats counters
# ================================================================== #
def test_stats_track_ingest_evaluate_fire():
    fired = []
    rt = build_runtime(on_intent=lambda i: fired.append(i))
    rt.ingest(make_signal(source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(source=SignalSource.KRONOS))
    rt.evaluate_all()
    rt.evaluate_all()  # second tick — same buffer, still fires

    s = rt.stats()
    assert s["ingested"] == 2
    assert s["ticks"] == 2
    assert s["intents_fired"] >= 1


def test_reset_stats_clears_counters():
    rt = build_runtime()
    rt.ingest(make_signal())
    rt.evaluate_all()
    rt.reset_stats()
    s = rt.stats()
    for k, v in s.items():
        assert v == 0


# ================================================================== #
# Expired signals trimmed before evaluate
# ================================================================== #
def test_evaluate_trims_expired_before_fusing():
    """A signal that expired between ingest and evaluate should not contribute."""
    rt = build_runtime()
    fresh_ts = datetime.now(timezone.utc)
    # Two fresh signals (will fire)
    rt.ingest(make_signal(ts=fresh_ts, source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(ts=fresh_ts, source=SignalSource.KRONOS))
    # One ancient signal
    rt.ingest(make_signal(
        ts=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_in_min=30,
        source=SignalSource.MACRO,
        direction=Direction.SHORT,
    ))

    intents = rt.evaluate_all()
    # The expired short signal should be trimmed; long entry should fire
    assert len(intents) == 1
    assert intents[0].direction == Direction.LONG


def test_only_expired_signals_no_intent():
    """Buffer with only expired signals → no intent (and no crash)."""
    rt = build_runtime()
    old_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    rt.ingest(make_signal(ts=old_ts, expires_in_min=30, source=SignalSource.SMART_MONEY))
    rt.ingest(make_signal(ts=old_ts, expires_in_min=30, source=SignalSource.KRONOS))

    intents = rt.evaluate_all()
    assert intents == []
    # Both should have been counted as expired_dropped during eval trim
    assert rt.stats()["expired_dropped"] >= 2
