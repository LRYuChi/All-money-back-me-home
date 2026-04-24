"""StrategyDef + context → StrategyIntent | None.

Evaluation order (per side):
  1. all_of  — every predicate must hold (skip block if empty → vacuously true)
  2. any_of  — at least one predicate must hold (skip block if empty → vacuously true)
  3. none_of — none of these may hold

If both long and short fire, return None — that's an internal conflict the
strategy author should fix (typically by sharpening predicates). We log
the conflict so it shows up in observability.

Position sizing (v1):
  - fixed_usd  — return that USD amount directly
  - fixed_pct  — context['capital'] * pct  (requires `capital` in context)
  - kelly      — context['kelly_stats'] = {win_rate, avg_win, avg_loss}
                 + kelly_fraction; if stats unavailable, fall back to
                 fixed_usd if also set, else None (skip the trade)

Result is clamped by max_size_usd. max_leverage is preserved on the
intent for L5 (risk layer) to apply when sizing the actual order.
"""
from __future__ import annotations

import logging
from typing import Any

from shared.signals.types import (
    Direction,
    FusedSignal,
    StrategyIntent,
)
from strategy_engine.predicates import evaluate_predicate
from strategy_engine.types import EntryRules, StrategyDef

logger = logging.getLogger(__name__)


class EvaluationError(RuntimeError):
    """Evaluator hit something it cannot recover from (e.g. conflicting sides)."""


def evaluate(
    strategy: StrategyDef,
    context: dict[str, Any],
    *,
    fused_signal: FusedSignal | None = None,
) -> StrategyIntent | None:
    """Evaluate one strategy against a context.

    Returns:
        StrategyIntent if entry conditions fired and sizing produced a
        positive notional. None otherwise (skip / no-action).

    Args:
        strategy: parsed StrategyDef
        context: dict with all signal data ('fused', 'kronos', 'smart_money',
            'macro', 'regime', 'capital', 'kelly_stats', ...). Predicates
            reference fields by dotted path.
        fused_signal: optional FusedSignal to attach to the intent for
            audit. If None, builds a placeholder from `context['fused']`.
    """
    if not strategy.enabled:
        return None

    long_fires = _entry_fires(strategy.entry_long, context)
    short_fires = _entry_fires(strategy.entry_short, context)

    if long_fires and short_fires:
        logger.warning(
            "strategy %s: both long+short entry fired — conflict, skipping",
            strategy.id,
        )
        return None

    if not long_fires and not short_fires:
        return None

    direction = Direction.LONG if long_fires else Direction.SHORT

    notional = _compute_size(strategy, context)
    if notional is None or notional <= 0:
        logger.info(
            "strategy %s: %s entry fired but sizing returned %s — skipping",
            strategy.id, direction.value, notional,
        )
        return None

    fs = fused_signal or _placeholder_fused(strategy, context, direction)

    return StrategyIntent(
        strategy_id=strategy.id,
        symbol=strategy.symbol,
        direction=direction,
        target_notional_usd=notional,
        entry_price_ref=_optional_float(context.get("price")),
        stop_loss_pct=strategy.exit.stop_loss,
        take_profit_pct=strategy.exit.take_profit,
        source_fused=fs,
    )


# ================================================================== #
# Exit decisions — for an OPEN position, decide whether to close
# ================================================================== #
def should_exit(
    strategy: StrategyDef,
    context: dict[str, Any],
    *,
    age_hours: float | None = None,
) -> tuple[bool, str | None]:
    """Check if any exit trigger fires. Returns (should_close, reason).

    Triggers:
      - any predicate in `exit.exit_on` matching → close
      - `time_stop_hours` exceeded → close
      - SL/TP are price-based and live in L6 execution layer, not here
    """
    if strategy.exit.time_stop_hours is not None and age_hours is not None:
        if age_hours >= strategy.exit.time_stop_hours:
            return True, "time_stop"

    for pred in strategy.exit.exit_on:
        try:
            if evaluate_predicate(pred, context):
                return True, f"exit_predicate:{pred}"
        except Exception as e:
            logger.warning("strategy %s exit predicate %r failed: %s", strategy.id, pred, e)

    return False, None


# ================================================================== #
# Helpers
# ================================================================== #
def _entry_fires(rules: EntryRules | None, context: dict[str, Any]) -> bool:
    """Block-structured evaluation. Empty blocks evaluate as PASS."""
    if rules is None:
        return False

    # all_of: every predicate must hold (empty → True)
    for p in rules.all_of:
        if not evaluate_predicate(p, context):
            return False

    # any_of: at least one must hold (empty → True / vacuously satisfied)
    if rules.any_of:
        if not any(evaluate_predicate(p, context) for p in rules.any_of):
            return False

    # none_of: none may hold (empty → True)
    for p in rules.none_of:
        if evaluate_predicate(p, context):
            return False

    return True


def _compute_size(strategy: StrategyDef, context: dict[str, Any]) -> float | None:
    """Apply position_sizing.method, then clamp by max_size_usd."""
    ps = strategy.position_sizing
    raw: float | None = None

    if ps.method == "fixed_usd":
        raw = ps.fixed_usd
    elif ps.method == "fixed_pct":
        capital = context.get("capital")
        if capital is None or ps.fixed_pct is None:
            return None
        raw = float(capital) * float(ps.fixed_pct)
    elif ps.method == "kelly":
        raw = _kelly_size(ps, context)

    if raw is None:
        return None

    if ps.max_size_usd is not None:
        raw = min(raw, ps.max_size_usd)
    return raw


def _kelly_size(ps, context: dict[str, Any]) -> float | None:
    """Fractional Kelly: f* = win_rate - (1 - win_rate) / (avg_win / avg_loss)
    Capital × kelly_fraction × max(0, f*).

    Requires:
      context['capital']
      context['kelly_stats'] = {win_rate, avg_win, avg_loss}
    Falls back to ps.fixed_usd if stats incomplete (lets a strategy run
    before the reflection loop has data — uses fixed sizing until then).
    """
    capital = context.get("capital")
    stats = context.get("kelly_stats") or {}
    if not capital or not stats:
        return ps.fixed_usd  # fallback (may itself be None → caller skips)

    try:
        wr = float(stats["win_rate"])
        avg_w = float(stats["avg_win"])
        avg_l = float(stats["avg_loss"])
    except (KeyError, ValueError, TypeError):
        return ps.fixed_usd

    if avg_l <= 0:
        return ps.fixed_usd

    b = avg_w / avg_l
    if b <= 0:
        return ps.fixed_usd

    f_star = wr - (1 - wr) / b
    if f_star <= 0:
        return None  # negative Kelly = don't trade

    fraction = ps.kelly_fraction or 0.25
    return float(capital) * fraction * f_star


def _optional_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _placeholder_fused(
    strategy: StrategyDef, context: dict[str, Any], direction: Direction,
) -> FusedSignal:
    """Last-resort FusedSignal when caller didn't provide one.

    Used in tests + standalone evaluator runs. In production the fusion
    layer always provides a real FusedSignal — this just keeps StrategyIntent
    construction non-optional.
    """
    fused_ctx = context.get("fused") or {}
    return FusedSignal(
        symbol=strategy.symbol,
        horizon=strategy.timeframe,
        direction=direction,
        ensemble_score=float(fused_ctx.get("ensemble_score", 0.5)),
        regime=str(context.get("regime", "UNKNOWN")),
        sources_count=int(fused_ctx.get("sources_count", 0)),
        contributions=dict(fused_ctx.get("contributions") or {}),
        conflict=bool(fused_ctx.get("conflict", False)),
    )


__all__ = ["EvaluationError", "evaluate", "should_exit"]
