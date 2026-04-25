"""Tests for YamlSymbolCatalog + build_symbol_catalog factory (round 35)."""
from __future__ import annotations

from pathlib import Path

import pytest

from execution.exchanges import (
    InMemorySymbolCatalog,
    NoOpSymbolCatalog,
    YamlSymbolCatalog,
    build_symbol_catalog,
)


# ================================================================== #
# YamlSymbolCatalog — happy path
# ================================================================== #
def test_yaml_loads_symbols(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text(
        "symbols:\n"
        "  - crypto:OKX:BTC/USDT:USDT\n"
        "  - crypto:OKX:ETH/USDT:USDT\n"
        "  - crypto:OKX:SOL/USDT:USDT\n"
    )
    c = YamlSymbolCatalog.from_path(f)
    assert c.supports("crypto:OKX:BTC/USDT:USDT")
    assert c.supports("crypto:OKX:ETH/USDT:USDT")
    assert c.supports("crypto:OKX:SOL/USDT:USDT")
    assert not c.supports("crypto:OKX:GHOST/USDT:USDT")
    assert len(c.all_supported()) == 3


def test_yaml_empty_symbols_block(tmp_path):
    """Empty list = nothing supported (G2 will deny all). Edge case
    worth supporting cleanly."""
    f = tmp_path / "empty.yaml"
    f.write_text("symbols: []\n")
    c = YamlSymbolCatalog.from_path(f)
    assert c.all_supported() == set()


def test_yaml_missing_symbols_key_treated_as_empty(tmp_path):
    f = tmp_path / "no_symbols_key.yaml"
    f.write_text("other_key: value\n")
    c = YamlSymbolCatalog.from_path(f)
    assert c.all_supported() == set()


def test_yaml_coerces_non_string_to_string(tmp_path):
    """Defensive: YAML may give us int/float; force to str so dict lookup works."""
    f = tmp_path / "mixed.yaml"
    f.write_text("symbols:\n  - 12345\n  - crypto:OKX:BTC/USDT:USDT\n")
    c = YamlSymbolCatalog.from_path(f)
    assert "12345" in c.all_supported()


# ================================================================== #
# YamlSymbolCatalog — error paths
# ================================================================== #
def test_yaml_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        YamlSymbolCatalog.from_path(tmp_path / "nope.yaml")


def test_yaml_top_level_must_be_mapping(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        YamlSymbolCatalog.from_path(f)


def test_yaml_symbols_must_be_list(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("symbols:\n  not: a list\n")
    with pytest.raises(ValueError, match="must be a list"):
        YamlSymbolCatalog.from_path(f)


# ================================================================== #
# build_symbol_catalog factory
# ================================================================== #
def test_factory_returns_noop_when_path_unset():
    class S:
        symbol_catalog_path = ""
    assert isinstance(build_symbol_catalog(S()), NoOpSymbolCatalog)


def test_factory_returns_noop_when_attribute_missing():
    """Defensive: settings without symbol_catalog_path attr → NoOp,
    not crash."""
    class S:
        pass
    assert isinstance(build_symbol_catalog(S()), NoOpSymbolCatalog)


def test_factory_returns_yaml_when_path_set(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text("symbols:\n  - crypto:OKX:BTC/USDT:USDT\n")

    class S:
        symbol_catalog_path = str(f)
    c = build_symbol_catalog(S())
    assert isinstance(c, YamlSymbolCatalog)
    assert c.supports("crypto:OKX:BTC/USDT:USDT")


def test_factory_falls_back_to_noop_on_missing_file(tmp_path):
    """Missing file at the configured path must NOT crash startup —
    G2 just fail-opens until ops fixes the path."""
    class S:
        symbol_catalog_path = str(tmp_path / "missing.yaml")
    c = build_symbol_catalog(S())
    assert isinstance(c, NoOpSymbolCatalog)


def test_factory_falls_back_to_noop_on_malformed_yaml(tmp_path):
    f = tmp_path / "broken.yaml"
    f.write_text("symbols: not_a_list\n")

    class S:
        symbol_catalog_path = str(f)
    c = build_symbol_catalog(S())
    assert isinstance(c, NoOpSymbolCatalog)


def test_factory_strips_whitespace_in_path(tmp_path):
    """Env vars sometimes have stray whitespace; strip before checking."""
    f = tmp_path / "catalog.yaml"
    f.write_text("symbols:\n  - X\n")

    class S:
        symbol_catalog_path = f"  {f}  "
    c = build_symbol_catalog(S())
    assert isinstance(c, YamlSymbolCatalog)
    assert c.supports("X")


# ================================================================== #
# Integration: YamlSymbolCatalog → SymbolSupportedGuard → pipeline
# ================================================================== #
def test_yaml_catalog_with_g2_denies_unknown_symbol(tmp_path):
    from execution.pending_orders.types import PendingOrder
    from risk import GuardContext, GuardResult, SymbolSupportedGuard

    f = tmp_path / "catalog.yaml"
    f.write_text("symbols:\n  - crypto:OKX:BTC/USDT:USDT\n")
    catalog = YamlSymbolCatalog.from_path(f)
    g = SymbolSupportedGuard(catalog=catalog)

    order_known = PendingOrder(
        strategy_id="s", symbol="crypto:OKX:BTC/USDT:USDT", side="long",
        target_notional_usd=100, mode="shadow",
    )
    order_unknown = PendingOrder(
        strategy_id="s", symbol="crypto:OKX:GHOST/USDT:USDT", side="long",
        target_notional_usd=100, mode="shadow",
    )
    ctx = GuardContext(capital_usd=10_000)

    assert g.check(order_known, ctx).result == GuardResult.ALLOW
    assert g.check(order_unknown, ctx).result == GuardResult.DENY
