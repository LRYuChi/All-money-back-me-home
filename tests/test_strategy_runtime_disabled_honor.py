"""Tests for round 28: StrategyRuntime honors disabled flag (incl. cache)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from time import sleep

import pytest

from fusion import Regime, SignalFuser
from shared.signals.types import (
    Direction,
    SignalSource,
    UniversalSignal,
)
from strategy_engine import (
    InMemoryStrategyRegistry,
    StrategyRuntime,
)


YAML = """
id: tr_disabled_v1
market: crypto
symbol: BTC
timeframe: 1h
enabled: true
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
position_sizing:
  method: fixed_usd
  fixed_usd: 100
"""


def _make_signal() -> UniversalSignal:
    return UniversalSignal(
        source=SignalSource.SMART_MONEY,
        symbol="BTC",
        horizon="1h",
        direction=Direction.LONG,
        strength=0.8,
        reason="test signal for runtime",
        ts=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def _trivial_weights() -> dict[str, dict[str, float]]:
    """Minimal weights matrix: one regime, all sources weighted equally."""
    return {
        Regime.BULL_TRENDING.value: {
            "kronos": 0.2, "smart_money": 0.4, "ta": 0.2,
            "ai_llm": 0.1, "macro": 0.1,
        },
    }


def _make_runtime(reg, *, registry_refresh_sec=None):
    return StrategyRuntime(
        registry=reg,
        fuser=SignalFuser(_trivial_weights()),
        regime_provider=lambda: Regime.BULL_TRENDING,
        capital_provider=lambda: 10_000,
        on_intent=lambda i: None,
        registry_refresh_sec=registry_refresh_sec,
    )


# ================================================================== #
# No cache: every tick re-queries
# ================================================================== #
def test_no_cache_calls_list_active_every_tick():
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg)

    rt.ingest(_make_signal())
    rt.evaluate_all()
    rt.evaluate_all()
    rt.evaluate_all()

    assert rt.stats()["registry_calls"] == 3
    assert rt.stats()["registry_cache_hits"] == 0


def test_no_cache_disabled_strategy_stops_firing_immediately():
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg)

    rt.ingest(_make_signal())
    intents_a = rt.evaluate_all()
    assert len(intents_a) == 1

    reg.set_enabled("tr_disabled_v1", False, reason="test")

    rt.ingest(_make_signal())
    intents_b = rt.evaluate_all()
    assert intents_b == []


# ================================================================== #
# TTL cache
# ================================================================== #
def test_cache_hits_within_ttl():
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg, registry_refresh_sec=10.0)

    rt.evaluate_all()
    rt.evaluate_all()
    rt.evaluate_all()

    assert rt.stats()["registry_calls"] == 1   # only first
    assert rt.stats()["registry_cache_hits"] == 2


def test_cache_expires_after_ttl():
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg, registry_refresh_sec=0.05)

    rt.evaluate_all()
    sleep(0.1)
    rt.evaluate_all()

    assert rt.stats()["registry_calls"] == 2


def test_refresh_strategies_busts_cache():
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg, registry_refresh_sec=60.0)

    rt.evaluate_all()
    rt.refresh_strategies()
    rt.evaluate_all()

    assert rt.stats()["registry_calls"] == 2


def test_cache_disabled_after_refresh_stops_intents():
    """Realistic flow: G9 trips → handler calls set_enabled → caller
    invokes refresh_strategies() → next tick produces no intents."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg, registry_refresh_sec=60.0)

    rt.ingest(_make_signal())
    intents_a = rt.evaluate_all()
    assert len(intents_a) == 1

    reg.set_enabled("tr_disabled_v1", False, reason="g9")
    rt.refresh_strategies()

    rt.ingest(_make_signal())
    intents_b = rt.evaluate_all()
    assert intents_b == []


# ================================================================== #
# Defence-in-depth: stale cache still honored
# ================================================================== #
def test_stale_cache_skips_disabled_via_runtime_check():
    """Even if cache is stale (we deliberately don't refresh), the
    runtime's per-tick `parsed.enabled` check stops disabled strategies
    from firing intents."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(YAML)
    rt = _make_runtime(reg, registry_refresh_sec=60.0)

    rt.ingest(_make_signal())
    intents_a = rt.evaluate_all()
    assert len(intents_a) == 1

    # Reach into cache and overwrite enabled flag on the cached record
    # to simulate "DB says disabled, cache hasn't refreshed yet"
    import dataclasses
    cached_rec = rt._cached_active[0]
    rt._cached_active[0] = dataclasses.replace(
        cached_rec,
        parsed=dataclasses.replace(cached_rec.parsed, enabled=False),
    )

    rt.ingest(_make_signal())
    intents_b = rt.evaluate_all()
    assert intents_b == []
    assert rt.stats()["skipped_disabled_runtime_check"] == 1


# ================================================================== #
# No cache by default = original behavior preserved
# ================================================================== #
def test_default_construction_disables_cache():
    reg = InMemoryStrategyRegistry()
    rt = _make_runtime(reg)
    assert rt._registry_refresh_sec is None
    rt.evaluate_all()
    rt.evaluate_all()
    assert rt.stats()["registry_calls"] == 2
    assert rt.stats()["registry_cache_hits"] == 0
