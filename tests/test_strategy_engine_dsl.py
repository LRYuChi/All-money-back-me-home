"""Tests for strategy_engine.dsl — YAML loader + schema validator."""
from __future__ import annotations

import pytest

from strategy_engine import (
    DSLError,
    EntryRules,
    PositionSizing,
    StrategyDef,
    load_strategy,
    load_strategy_str,
)


VALID_YAML = """
id: crypto_btc_v1
market: crypto
symbol: "crypto:OKX:BTC/USDT:USDT"
timeframe: 15m
enabled: true
mode: shadow

entry:
  long:
    all_of:
      - fused.direction == "long"
      - fused.ensemble_score >= 0.6
    none_of:
      - regime in ["CRISIS"]

position_sizing:
  method: kelly
  kelly_fraction: 0.25
  max_size_usd: 500
  max_leverage: 2

exit:
  stop_loss: 0.02
  exit_on:
    - fused.direction == "short"
  time_stop_hours: 48

description: "Test strategy"
tags:
  - btc
  - kelly
"""


# ================================================================== #
# Happy path
# ================================================================== #
def test_load_full_strategy():
    s = load_strategy_str(VALID_YAML)
    assert isinstance(s, StrategyDef)
    assert s.id == "crypto_btc_v1"
    assert s.market == "crypto"
    assert s.timeframe == "15m"
    assert s.enabled is True
    assert s.mode == "shadow"

    assert s.entry_long is not None
    assert s.entry_short is None
    assert len(s.entry_long.all_of) == 2
    assert len(s.entry_long.none_of) == 1

    assert s.position_sizing.method == "kelly"
    assert s.position_sizing.kelly_fraction == 0.25
    assert s.position_sizing.max_size_usd == 500
    assert s.position_sizing.max_leverage == 2

    assert s.exit.stop_loss == 0.02
    assert s.exit.time_stop_hours == 48
    assert "btc" in s.tags


def test_load_minimal_strategy():
    """Minimum required: id/market/symbol/timeframe + at least one entry."""
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - fused.direction == "long"
"""
    s = load_strategy_str(yaml_text)
    assert s.id == "x"
    assert s.entry_short is None


# ================================================================== #
# Required field validation
# ================================================================== #
@pytest.mark.parametrize("missing", ["id", "market", "symbol", "timeframe"])
def test_missing_required_field_raises(missing):
    parts = {
        "id": "x", "market": "crypto", "symbol": "BTC", "timeframe": "1h",
        "entry": {"long": {"all_of": ['fused.direction == "long"']}},
    }
    parts.pop(missing)
    yaml_text = "\n".join(f"{k}: {v}" if isinstance(v, str) else f"{k}:" for k, v in parts.items())
    if missing != "id":  # have to be careful with YAML serialisation
        # Build manually without the missing key
        import yaml as _y
        yaml_text = _y.safe_dump(parts)
    with pytest.raises(DSLError, match=f"missing required field: '{missing}'"):
        load_strategy_str(yaml_text)


def test_invalid_timeframe_raises():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 30m
entry:
  long:
    all_of: [fused.direction == "long"]
"""
    with pytest.raises(DSLError, match="invalid timeframe"):
        load_strategy_str(yaml_text)


def test_invalid_mode_raises():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
mode: yolo
entry:
  long:
    all_of: [fused.direction == "long"]
"""
    with pytest.raises(DSLError, match="invalid mode"):
        load_strategy_str(yaml_text)


def test_no_entry_block_raises():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
"""
    with pytest.raises(DSLError, match="entry.long"):
        load_strategy_str(yaml_text)


def test_empty_entry_block_raises():
    """A block with no all_of/any_of/none_of = nothing to evaluate → reject."""
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {}
"""
    with pytest.raises(DSLError, match="no predicates"):
        load_strategy_str(yaml_text)


# ================================================================== #
# Predicate validation (catches bad expressions early)
# ================================================================== #
def test_malformed_predicate_in_entry_raises():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - "this is not a valid predicate"
"""
    with pytest.raises(DSLError, match="entry.long.all_of"):
        load_strategy_str(yaml_text)


def test_unsupported_compound_logic_raises_at_load():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - "a > 1 and b < 2"
"""
    with pytest.raises(DSLError):
        load_strategy_str(yaml_text)


def test_predicate_must_be_string():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 42
"""
    with pytest.raises(DSLError, match="must be a string predicate"):
        load_strategy_str(yaml_text)


# ================================================================== #
# Position sizing
# ================================================================== #
def test_sizing_kelly_requires_fraction():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
position_sizing:
  method: kelly
"""
    with pytest.raises(DSLError, match="kelly_fraction"):
        load_strategy_str(yaml_text)


def test_sizing_fixed_usd_requires_amount():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
position_sizing:
  method: fixed_usd
"""
    with pytest.raises(DSLError, match="fixed_usd"):
        load_strategy_str(yaml_text)


def test_sizing_invalid_method_raises():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
position_sizing:
  method: martingale
"""
    with pytest.raises(DSLError, match="position_sizing.method"):
        load_strategy_str(yaml_text)


def test_sizing_default_when_omitted():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
"""
    s = load_strategy_str(yaml_text)
    assert isinstance(s.position_sizing, PositionSizing)
    # Default is fixed_usd but with no amount → still parses (not used until execution)
    assert s.position_sizing.method == "fixed_usd"


# ================================================================== #
# Exit rules
# ================================================================== #
def test_exit_optional_when_omitted():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
"""
    s = load_strategy_str(yaml_text)
    assert s.exit.stop_loss is None
    assert s.exit.exit_on == ()


def test_exit_validates_predicates():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ["fused.direction == \\"long\\""]}
exit:
  exit_on:
    - "garbage syntax"
"""
    with pytest.raises(DSLError, match="exit.exit_on"):
        load_strategy_str(yaml_text)


# ================================================================== #
# load_strategy from disk
# ================================================================== #
def test_load_strategy_missing_file(tmp_path):
    with pytest.raises(DSLError, match="not found"):
        load_strategy(tmp_path / "nope.yaml")


def test_load_strategy_from_disk(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text(VALID_YAML)
    s = load_strategy(f)
    assert s.id == "crypto_btc_v1"


# ================================================================== #
# YAML parse errors surface clearly
# ================================================================== #
def test_invalid_yaml_raises_dsl_error():
    with pytest.raises(DSLError, match="YAML parse"):
        load_strategy_str("id: [\nunclosed: bracket")


def test_top_level_not_dict_raises():
    with pytest.raises(DSLError, match="top-level"):
        load_strategy_str("- just\n- a\n- list")
