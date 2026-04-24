"""Tests for strategy_engine.registry — InMemoryStrategyRegistry + factory."""
from __future__ import annotations

import pytest

from strategy_engine import (
    DSLError,
    InMemoryStrategyRegistry,
    StrategyNotFound,
    build_registry,
)


VALID_YAML = """
id: test_strat_v1
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
"""


# ================================================================== #
# InMemory CRUD
# ================================================================== #
def test_upsert_creates_record():
    reg = InMemoryStrategyRegistry()
    rec = reg.upsert(VALID_YAML)
    assert rec.id == "test_strat_v1"
    assert rec.parsed.id == "test_strat_v1"
    assert rec.created_at == rec.updated_at  # first write


def test_upsert_updates_record_preserves_created_at():
    reg = InMemoryStrategyRegistry()
    r1 = reg.upsert(VALID_YAML)
    # Modify (e.g. change tags) and upsert again
    modified = VALID_YAML + "\ntags:\n  - new\n"
    r2 = reg.upsert(modified)

    assert r2.created_at == r1.created_at
    assert r2.updated_at > r1.created_at  # rotation marker
    assert "new" in r2.parsed.tags


def test_upsert_invalid_yaml_raises_dsl_error():
    reg = InMemoryStrategyRegistry()
    with pytest.raises(DSLError):
        reg.upsert("not: valid: yaml: at: all")


def test_upsert_invalid_strategy_raises_dsl_error():
    """Bad strategy YAML never lands in DB."""
    reg = InMemoryStrategyRegistry()
    bad = """
id: x
market: crypto
symbol: BTC
timeframe: 30m
entry:
  long: {all_of: ['fused.direction == "long"']}
"""
    with pytest.raises(DSLError, match="invalid timeframe"):
        reg.upsert(bad)
    # Confirm nothing was stored
    assert reg.list_all() == []


def test_get_returns_record():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    rec = reg.get("test_strat_v1")
    assert rec.id == "test_strat_v1"


def test_get_unknown_raises_not_found():
    reg = InMemoryStrategyRegistry()
    with pytest.raises(StrategyNotFound):
        reg.get("nope")


def test_delete_returns_true_when_existed():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    assert reg.delete("test_strat_v1") is True
    assert reg.delete("test_strat_v1") is False  # idempotent


def test_list_all_returns_all():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    second = VALID_YAML.replace("test_strat_v1", "test_strat_v2")
    reg.upsert(second)
    assert {r.id for r in reg.list_all()} == {"test_strat_v1", "test_strat_v2"}


def test_list_active_filters_disabled():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    disabled = VALID_YAML.replace(
        "id: test_strat_v1", "id: disabled_v1"
    ) + "\nenabled: false\n"
    reg.upsert(disabled)

    active = reg.list_active()
    assert len(active) == 1
    assert active[0].id == "test_strat_v1"


# ================================================================== #
# Factory
# ================================================================== #
def test_factory_inmemory_fallback():
    class S:
        database_url = ""
        supabase_url = ""
        supabase_service_key = ""

    reg = build_registry(S())
    assert isinstance(reg, InMemoryStrategyRegistry)


def test_factory_postgres_when_dsn_set():
    from strategy_engine import PostgresStrategyRegistry

    class S:
        database_url = "postgresql://x"
        supabase_url = ""
        supabase_service_key = ""

    reg = build_registry(S())
    assert isinstance(reg, PostgresStrategyRegistry)
