"""Tests for strategy_engine.evaluator — evaluate() + should_exit()."""
from __future__ import annotations

import pytest

from shared.signals.types import Direction, FusedSignal
from strategy_engine import evaluate, load_strategy_str, should_exit


# ================================================================== #
# Helper: build minimal strategies via YAML for realism
# ================================================================== #
def long_only_yaml(**overrides) -> str:
    base = {
        "id": "test_v1", "market": "crypto", "symbol": "BTC",
        "timeframe": "1h", "enabled": True,
        "entry_long_all": ['fused.direction == "long"', 'fused.ensemble_score >= 0.6'],
        "sizing_method": "fixed_usd",
        "sizing_amount": 500,
    }
    base.update(overrides)
    return f"""
id: {base["id"]}
market: {base["market"]}
symbol: {base["symbol"]}
timeframe: {base["timeframe"]}
enabled: {str(base["enabled"]).lower()}
entry:
  long:
    all_of:
{chr(10).join(f"      - {p!r}" for p in base["entry_long_all"])}
position_sizing:
  method: {base["sizing_method"]}
  fixed_usd: {base["sizing_amount"]}
"""


def make_context(direction="long", ensemble_score=0.7, **extra):
    ctx = {
        "fused": {
            "direction": direction,
            "ensemble_score": ensemble_score,
            "sources_count": 3,
            "contributions": {"smart_money": 0.4},
            "conflict": False,
        },
        "regime": "BULL_TRENDING",
    }
    ctx.update(extra)
    return ctx


# ================================================================== #
# Long entry fires
# ================================================================== #
def test_long_entry_fires_when_predicates_pass():
    s = load_strategy_str(long_only_yaml())
    intent = evaluate(s, make_context(direction="long", ensemble_score=0.7))

    assert intent is not None
    assert intent.direction == Direction.LONG
    assert intent.target_notional_usd == 500
    assert intent.strategy_id == "test_v1"
    assert intent.symbol == "BTC"


def test_long_entry_skips_when_score_too_low():
    s = load_strategy_str(long_only_yaml())
    intent = evaluate(s, make_context(direction="long", ensemble_score=0.4))
    assert intent is None


def test_long_entry_skips_when_direction_short():
    s = load_strategy_str(long_only_yaml())
    intent = evaluate(s, make_context(direction="short", ensemble_score=0.9))
    assert intent is None


# ================================================================== #
# Short entry
# ================================================================== #
def test_short_entry_fires():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  short:
    all_of:
      - 'fused.direction == "short"'
      - fused.ensemble_score >= 0.6
position_sizing:
  method: fixed_usd
  fixed_usd: 200
"""
    s = load_strategy_str(yaml_text)
    intent = evaluate(s, make_context(direction="short", ensemble_score=0.7))
    assert intent is not None
    assert intent.direction == Direction.SHORT
    assert intent.target_notional_usd == 200


# ================================================================== #
# Conflict: both sides fire → return None + warn
# ================================================================== #
def test_conflict_when_both_long_and_short_fire(caplog):
    """Both sides fire on the same context — strategy author bug, evaluator
    refuses to pick a side."""
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 'fused.direction != "neutral"'
  short:
    all_of:
      - 'fused.direction != "neutral"'
position_sizing:
  method: fixed_usd
  fixed_usd: 100
"""
    import logging
    s = load_strategy_str(yaml_text)
    with caplog.at_level(logging.WARNING):
        intent = evaluate(s, make_context(direction="long"))
    assert intent is None
    assert any("conflict" in r.message.lower() for r in caplog.records)


# ================================================================== #
# Block-structured logic
# ================================================================== #
def test_any_of_passes_when_at_least_one_holds():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
    any_of:
      - fused.ensemble_score >= 0.9
      - smart_money.count >= 2
position_sizing:
  method: fixed_usd
  fixed_usd: 100
"""
    s = load_strategy_str(yaml_text)
    # ensemble too low for first any_of, but smart_money.count satisfies
    ctx = make_context(direction="long", ensemble_score=0.65)
    ctx["smart_money"] = {"count": 3}
    assert evaluate(s, ctx) is not None


def test_any_of_fails_when_all_fail():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
    any_of:
      - fused.ensemble_score >= 0.9
      - smart_money.count >= 5
position_sizing:
  method: fixed_usd
  fixed_usd: 100
"""
    s = load_strategy_str(yaml_text)
    ctx = make_context(direction="long", ensemble_score=0.7)
    ctx["smart_money"] = {"count": 2}
    assert evaluate(s, ctx) is None


def test_none_of_blocks_entry():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
    none_of:
      - 'regime == "CRISIS"'
position_sizing:
  method: fixed_usd
  fixed_usd: 100
"""
    s = load_strategy_str(yaml_text)
    crisis_ctx = make_context(direction="long")
    crisis_ctx["regime"] = "CRISIS"
    assert evaluate(s, crisis_ctx) is None


# ================================================================== #
# Position sizing
# ================================================================== #
def test_sizing_fixed_pct():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: fixed_pct
  fixed_pct: 0.10
"""
    s = load_strategy_str(yaml_text)
    intent = evaluate(s, {**make_context(), "capital": 5000})
    assert intent is not None
    assert intent.target_notional_usd == 500.0


def test_sizing_fixed_pct_no_capital_skips():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: fixed_pct
  fixed_pct: 0.1
"""
    s = load_strategy_str(yaml_text)
    intent = evaluate(s, make_context())   # no capital
    assert intent is None


def test_sizing_max_size_clamps():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: fixed_pct
  fixed_pct: 0.5
  max_size_usd: 200
"""
    s = load_strategy_str(yaml_text)
    intent = evaluate(s, {**make_context(), "capital": 1000})
    assert intent is not None
    assert intent.target_notional_usd == 200   # clamped


def test_sizing_kelly_with_stats():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: kelly
  kelly_fraction: 0.25
  fixed_usd: 100
"""
    s = load_strategy_str(yaml_text)
    ctx = make_context()
    ctx["capital"] = 10_000
    ctx["kelly_stats"] = {"win_rate": 0.6, "avg_win": 1.0, "avg_loss": 1.0}
    # f* = 0.6 - 0.4/1 = 0.2
    # size = 10000 * 0.25 * 0.2 = 500
    intent = evaluate(s, ctx)
    assert intent is not None
    assert intent.target_notional_usd == pytest.approx(500.0)


def test_sizing_kelly_negative_skips():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: kelly
  kelly_fraction: 0.25
"""
    s = load_strategy_str(yaml_text)
    ctx = make_context()
    ctx["capital"] = 10_000
    # win_rate too low to be profitable
    ctx["kelly_stats"] = {"win_rate": 0.3, "avg_win": 1.0, "avg_loss": 1.0}
    intent = evaluate(s, ctx)
    assert intent is None


def test_sizing_kelly_falls_back_to_fixed_when_stats_missing():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long:
    all_of: ['fused.direction == "long"']
position_sizing:
  method: kelly
  kelly_fraction: 0.25
  fixed_usd: 150
"""
    s = load_strategy_str(yaml_text)
    intent = evaluate(s, {**make_context(), "capital": 10_000})  # no kelly_stats
    # Falls back to fixed_usd
    assert intent is not None
    assert intent.target_notional_usd == 150


# ================================================================== #
# Disabled strategy
# ================================================================== #
def test_disabled_strategy_returns_none():
    s = load_strategy_str(long_only_yaml(enabled=False))
    intent = evaluate(s, make_context())
    assert intent is None


# ================================================================== #
# should_exit — exit_on predicates + time stop
# ================================================================== #
def test_exit_on_predicate_fires():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ['fused.direction == "long"']}
exit:
  exit_on:
    - 'fused.direction == "short"'
"""
    s = load_strategy_str(yaml_text)
    fired, reason = should_exit(s, make_context(direction="short"))
    assert fired is True
    assert "exit_predicate" in reason


def test_exit_no_trigger_no_close():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ['fused.direction == "long"']}
exit:
  exit_on:
    - 'fused.direction == "short"'
"""
    s = load_strategy_str(yaml_text)
    fired, reason = should_exit(s, make_context(direction="long"))
    assert fired is False
    assert reason is None


def test_time_stop_fires():
    yaml_text = """
id: x
market: crypto
symbol: BTC
timeframe: 1h
entry:
  long: {all_of: ['fused.direction == "long"']}
exit:
  time_stop_hours: 24
"""
    s = load_strategy_str(yaml_text)
    fired, reason = should_exit(s, make_context(), age_hours=25.0)
    assert fired is True
    assert reason == "time_stop"

    # Below threshold → no close
    fired2, _ = should_exit(s, make_context(), age_hours=20.0)
    assert fired2 is False
