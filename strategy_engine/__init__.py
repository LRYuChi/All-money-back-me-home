"""L4 Strategy DSL — YAML-defined strategies, evaluated against signal context.

A strategy is a YAML doc describing:
  - entry conditions (long / short)
  - position sizing
  - exit conditions (SL/TP + signal-driven)
  - meta (id / market / symbol / timeframe / enabled)

Evaluation flow at runtime (Phase E):
    fused_signal = fusion_layer.fuse(signals_for_symbol, regime)
    context = {
        "fused": {...}, "kronos": {...}, "smart_money": {...},
        "macro": {...}, "regime": "BULL_TRENDING",
    }
    intent = strategy.evaluate(context)  # → StrategyIntent or None

This round (8): schema parser + predicate language + 1-shot evaluator.
Coming rounds: position sizing (Kelly + Phase G integration) + exit
state machine (open intent + later close-trigger evaluation).

Predicate language (v1, minimal CEL subset):
    Atoms        ── true | false | 1.5 | "str" | a.b.c
    Compare      ── x == y | x != y | x < y | x <= y | x > y | x >= y
    Membership   ── x in [a, b, c]
    All others go through `all_of / any_of / none_of` block structure.

Why not a full expression evaluator: small surface = small bug count,
fast review, no eval() risk. Block structure handles compound logic.
"""

from strategy_engine.predicates import (
    PredicateError,
    UnsupportedExpression,
    evaluate_predicate,
    parse_predicate,
)
from strategy_engine.types import (
    EntryRules,
    ExitRules,
    PositionSizing,
    StrategyDef,
)
from strategy_engine.dsl import (
    DSLError,
    load_strategy,
    load_strategy_str,
)
from strategy_engine.evaluator import (
    EvaluationError,
    evaluate,
    should_exit,
)
from strategy_engine.registry import (
    InMemoryStrategyRegistry,
    PostgresStrategyRegistry,
    StrategyNotFound,
    StrategyRecord,
    StrategyRegistry,
    SupabaseStrategyRegistry,
    build_registry,
)
from strategy_engine.runtime import (
    CapitalProvider,
    IntentCallback,
    RegimeProvider,
    StrategyRuntime,
)

__all__ = [
    "CapitalProvider",
    "DSLError",
    "EntryRules",
    "EvaluationError",
    "ExitRules",
    "InMemoryStrategyRegistry",
    "IntentCallback",
    "PositionSizing",
    "PostgresStrategyRegistry",
    "PredicateError",
    "RegimeProvider",
    "StrategyDef",
    "StrategyNotFound",
    "StrategyRecord",
    "StrategyRegistry",
    "StrategyRuntime",
    "SupabaseStrategyRegistry",
    "UnsupportedExpression",
    "build_registry",
    "evaluate",
    "evaluate_predicate",
    "load_strategy",
    "load_strategy_str",
    "parse_predicate",
    "should_exit",
]
