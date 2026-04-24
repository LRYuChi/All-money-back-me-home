"""YAML strategy loader + schema validator.

Strict on required fields; lenient on extras (warns, doesn't crash).
Fail-loud on bad predicates — strategy authors get immediate feedback.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from strategy_engine.predicates import parse_predicate
from strategy_engine.types import (
    EntryRules,
    ExitRules,
    PositionSizing,
    PositionSizingMethod,
    StrategyDef,
)

logger = logging.getLogger(__name__)


class DSLError(ValueError):
    """Strategy YAML is malformed or violates the schema."""


_REQUIRED_TOP = ("id", "market", "symbol", "timeframe")
_VALID_TIMEFRAMES = ("15m", "1h", "4h", "1d")
_VALID_MODES = ("shadow", "paper", "live", "notify")
_VALID_SIZING_METHODS: tuple[PositionSizingMethod, ...] = (
    "fixed_usd", "fixed_pct", "kelly",
)


def load_strategy(path: Path | str) -> StrategyDef:
    """Load + parse + validate a strategy YAML from disk."""
    p = Path(path)
    if not p.exists():
        raise DSLError(f"strategy file not found: {p}")
    return load_strategy_str(p.read_text())


def load_strategy_str(yaml_text: str) -> StrategyDef:
    """Parse YAML string and return validated StrategyDef.

    Raises DSLError on:
      - YAML parse failure
      - missing required field
      - invalid timeframe / mode
      - malformed predicate (parse_predicate raises bubbled with context)
    """
    try:
        import yaml
    except ImportError as e:
        raise DSLError(f"pyyaml not installed: {e}") from e

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise DSLError(f"YAML parse error: {e}") from e

    if not isinstance(data, dict):
        raise DSLError(f"top-level must be a dict, got {type(data).__name__}")

    for key in _REQUIRED_TOP:
        if key not in data:
            raise DSLError(f"missing required field: {key!r}")

    timeframe = data["timeframe"]
    if timeframe not in _VALID_TIMEFRAMES:
        raise DSLError(
            f"invalid timeframe {timeframe!r}, must be one of {_VALID_TIMEFRAMES}"
        )

    mode = data.get("mode", "shadow")
    if mode not in _VALID_MODES:
        raise DSLError(f"invalid mode {mode!r}, must be one of {_VALID_MODES}")

    entry_long = _parse_entry_block(data.get("entry", {}).get("long"), "entry.long")
    entry_short = _parse_entry_block(data.get("entry", {}).get("short"), "entry.short")
    if entry_long is None and entry_short is None:
        raise DSLError("strategy must define at least one of entry.long / entry.short")

    sizing = _parse_position_sizing(data.get("position_sizing", {}))
    exit_rules = _parse_exit_rules(data.get("exit", {}))

    tags_raw = data.get("tags") or []
    if not isinstance(tags_raw, list):
        raise DSLError(f"tags must be a list, got {type(tags_raw).__name__}")

    return StrategyDef(
        id=str(data["id"]),
        market=str(data["market"]),
        symbol=str(data["symbol"]),
        timeframe=timeframe,
        enabled=bool(data.get("enabled", True)),
        mode=mode,
        entry_long=entry_long,
        entry_short=entry_short,
        position_sizing=sizing,
        exit=exit_rules,
        description=str(data.get("description", "")),
        tags=tuple(str(t) for t in tags_raw),
    )


# ================================================================== #
# Block parsers
# ================================================================== #
def _parse_entry_block(block: Any, ctx: str) -> EntryRules | None:
    if block is None:
        return None
    if not isinstance(block, dict):
        raise DSLError(f"{ctx} must be a dict, got {type(block).__name__}")
    all_of = _parse_predicate_list(block.get("all_of"), f"{ctx}.all_of")
    any_of = _parse_predicate_list(block.get("any_of"), f"{ctx}.any_of")
    none_of = _parse_predicate_list(block.get("none_of"), f"{ctx}.none_of")
    if not (all_of or any_of or none_of):
        raise DSLError(f"{ctx} block has no predicates")
    return EntryRules(all_of=all_of, any_of=any_of, none_of=none_of)


def _parse_predicate_list(items: Any, ctx: str) -> tuple[str, ...]:
    if items is None:
        return ()
    if not isinstance(items, list):
        raise DSLError(f"{ctx} must be a list, got {type(items).__name__}")
    out: list[str] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, str):
            raise DSLError(f"{ctx}[{i}] must be a string predicate, got {type(raw).__name__}")
        try:
            parse_predicate(raw)
        except Exception as e:
            raise DSLError(f"{ctx}[{i}] {raw!r}: {e}") from e
        out.append(raw)
    return tuple(out)


def _parse_position_sizing(block: Any) -> PositionSizing:
    if not block:
        return PositionSizing()
    if not isinstance(block, dict):
        raise DSLError(f"position_sizing must be a dict, got {type(block).__name__}")
    method = block.get("method", "fixed_usd")
    if method not in _VALID_SIZING_METHODS:
        raise DSLError(
            f"position_sizing.method {method!r} invalid; must be one of {_VALID_SIZING_METHODS}"
        )

    sizing = PositionSizing(
        method=method,
        fixed_usd=_optional_float(block.get("fixed_usd"), "position_sizing.fixed_usd"),
        fixed_pct=_optional_float(block.get("fixed_pct"), "position_sizing.fixed_pct"),
        kelly_fraction=_optional_float(block.get("kelly_fraction"), "position_sizing.kelly_fraction"),
        max_size_usd=_optional_float(block.get("max_size_usd"), "position_sizing.max_size_usd"),
        max_leverage=_optional_float(block.get("max_leverage"), "position_sizing.max_leverage") or 1.0,
    )

    # Method-specific required fields
    if method == "fixed_usd" and sizing.fixed_usd is None:
        raise DSLError("position_sizing.method=fixed_usd requires fixed_usd")
    if method == "fixed_pct" and sizing.fixed_pct is None:
        raise DSLError("position_sizing.method=fixed_pct requires fixed_pct")
    if method == "kelly" and sizing.kelly_fraction is None:
        raise DSLError("position_sizing.method=kelly requires kelly_fraction")
    return sizing


def _parse_exit_rules(block: Any) -> ExitRules:
    if not block:
        return ExitRules()
    if not isinstance(block, dict):
        raise DSLError(f"exit must be a dict, got {type(block).__name__}")
    return ExitRules(
        stop_loss=_optional_float(block.get("stop_loss"), "exit.stop_loss"),
        take_profit=_optional_float(block.get("take_profit"), "exit.take_profit"),
        exit_on=_parse_predicate_list(block.get("exit_on"), "exit.exit_on"),
        time_stop_hours=_optional_int(block.get("time_stop_hours"), "exit.time_stop_hours"),
    )


def _optional_float(value: Any, ctx: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise DSLError(f"{ctx} must be numeric, got {value!r}") from e


def _optional_int(value: Any, ctx: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise DSLError(f"{ctx} must be int, got {value!r}") from e


__all__ = ["DSLError", "load_strategy", "load_strategy_str"]
