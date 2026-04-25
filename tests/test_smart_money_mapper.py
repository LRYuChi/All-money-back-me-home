"""Tests for smart_money.execution.mapper (P4c)."""
from __future__ import annotations

from pathlib import Path

import pytest

from smart_money.execution.mapper import SymbolMapper


@pytest.fixture
def yaml_map(tmp_path: Path) -> Path:
    p = tmp_path / "sym.yaml"
    p.write_text("""
BTC:
  okx: "BTC/USDT:USDT"
  min_notional_usd: 10
ETH:
  okx: "ETH/USDT:USDT"
  min_notional_usd: 10
HYPE:
  okx: null
""")
    return p


# ------------------------------------------------------------------ #
# Load
# ------------------------------------------------------------------ #
def test_load_missing_file_returns_empty(tmp_path):
    m = SymbolMapper.load(tmp_path / "absent.yaml")
    assert m.known_symbols() == []


def test_load_parses_valid_entries(yaml_map):
    m = SymbolMapper.load(yaml_map)
    # HYPE has null okx → skipped
    assert set(m.known_symbols()) == {"BTC", "ETH"}


def test_load_real_config_file_parses_if_present():
    """Sanity check: if the shipped default exists, it's valid."""
    default = Path("config/smart_money/symbol_map.yaml")
    if not default.exists():
        pytest.skip("default config not present in this environment")
    m = SymbolMapper.load(default)
    # At minimum we expect BTC and ETH in the curated list
    assert "BTC" in m.known_symbols()
    assert "ETH" in m.known_symbols()


def test_high_volume_perps_must_be_mapped():
    """R80 regression guard — symbols added because live SHADOW telemetry
    showed significant unknown_symbol skip volume on them.

    Each entry here was added in response to a real ops incident. Removing
    one will reintroduce the silent skip pattern for that symbol.

    HYPE: 233/24h skips (2026-04-26) — Hyperliquid native token, single
          most-traded perp by a tracked whale wallet. OKX listed
          HYPE-USDT-SWAP 2025-02-21.
    """
    default = Path("config/smart_money/symbol_map.yaml")
    if not default.exists():
        pytest.skip("default config not present in this environment")
    m = SymbolMapper.load(default)
    must_have = ["BTC", "ETH", "SOL", "HYPE"]
    missing = [s for s in must_have if s not in m.known_symbols()]
    assert not missing, (
        f"R80 regression: high-volume perps missing from symbol_map: "
        f"{missing}. See test docstring for context."
    )


# ------------------------------------------------------------------ #
# Lookup
# ------------------------------------------------------------------ #
def test_lookup_known_symbol(yaml_map):
    m = SymbolMapper.load(yaml_map)
    e = m.lookup("BTC")
    assert e is not None
    assert e.okx == "BTC/USDT:USDT"
    assert e.min_notional_usd == 10


def test_lookup_unknown_symbol(yaml_map):
    m = SymbolMapper.load(yaml_map)
    assert m.lookup("FAKECOIN") is None


# ------------------------------------------------------------------ #
# check()
# ------------------------------------------------------------------ #
def test_check_unknown_symbol_fails(yaml_map):
    m = SymbolMapper.load(yaml_map)
    result = m.check("FAKECOIN", size_coin=1.0, px=100.0)
    assert result.ok is False
    assert result.reason == "unknown_symbol"
    assert result.okx_symbol is None
    assert result.entry is None


def test_check_below_min_notional_fails(yaml_map):
    m = SymbolMapper.load(yaml_map)
    # 0.0001 BTC * $50,000 = $5, below min 10
    result = m.check("BTC", size_coin=0.0001, px=50_000.0)
    assert result.ok is False
    assert result.reason == "below_min_size"
    # Mapper still fills in OKX symbol and notional for audit
    assert result.okx_symbol == "BTC/USDT:USDT"
    assert result.notional_usd == 5.0


def test_check_exactly_at_min_notional_passes(yaml_map):
    m = SymbolMapper.load(yaml_map)
    # $10 notional = exactly at threshold
    result = m.check("BTC", size_coin=0.0002, px=50_000.0)
    assert result.ok is True
    assert result.notional_usd == 10.0


def test_check_above_min_passes(yaml_map):
    m = SymbolMapper.load(yaml_map)
    result = m.check("BTC", size_coin=0.1, px=50_000.0)
    assert result.ok is True
    assert result.notional_usd == 5_000.0
    assert result.okx_symbol == "BTC/USDT:USDT"


# ------------------------------------------------------------------ #
# Robustness
# ------------------------------------------------------------------ #
def test_malformed_entry_is_skipped(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("""
BTC:
  okx: "BTC/USDT:USDT"
  min_notional_usd: 10
NOT_A_DICT: "just a string"
INCOMPLETE:
  min_notional_usd: 10
""")
    m = SymbolMapper.load(p)
    # Only BTC survives (NOT_A_DICT isn't a dict, INCOMPLETE missing okx)
    assert m.known_symbols() == ["BTC"]
