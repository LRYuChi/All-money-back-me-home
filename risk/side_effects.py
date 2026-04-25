"""Side-effect handlers for guard pipeline DENYs (round 26).

PendingOrderWorker calls a `GuardSideEffectHandler(order, decisions)`
after every DENY. This module ships built-in handlers for common patterns:

  - `make_g9_strategy_disabler(registry)` — when G9 ConsecutiveLossDays
    trips, disable the order's strategy in the registry. Audit row is
    written via `set_enabled(actor='guard:consecutive_loss_cb', ...)`.
    Idempotent: re-trips of an already-disabled strategy are skipped
    (no duplicate audit row, no duplicate log line).

  - `chain_handlers(*handlers)` — combine N handlers; each is called in
    order, and exceptions in one don't stop subsequent ones.

A handler may inspect any guard's decisions (not just G9) — the
GuardDecision objects are passed in the same order the pipeline saw them.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from execution.pending_orders.types import PendingOrder
from strategy_engine.registry import StrategyNotFound, StrategyRegistry

logger = logging.getLogger(__name__)


GuardSideEffectHandler = Callable[[PendingOrder, list], None]


def chain_handlers(*handlers: GuardSideEffectHandler) -> GuardSideEffectHandler:
    """Combine handlers into one; failures in one don't block the next."""

    def _chained(order: PendingOrder, decisions: list) -> None:
        for h in handlers:
            try:
                h(order, decisions)
            except Exception as e:
                logger.exception(
                    "chain_handlers: %s raised — continuing chain (%s)",
                    getattr(h, "__name__", repr(h)), e,
                )
    _chained.__name__ = (
        "chain_handlers["
        + ",".join(getattr(h, "__name__", "h") for h in handlers)
        + "]"
    )
    return _chained


def make_g9_strategy_disabler(
    registry: StrategyRegistry,
    *,
    actor: str = "guard:consecutive_loss_cb",
    only_if_currently_enabled: bool = True,
    extra_reason_prefix: str = "G9 trip:",
) -> GuardSideEffectHandler:
    """Return a handler that disables the order's strategy when G9 trips.

    Args:
        registry: target StrategyRegistry. set_enabled is called on it.
        actor: written to strategy_enable_history.actor.
        only_if_currently_enabled: skip if already disabled (avoids
            duplicate audit rows when subsequent orders also trip G9
            before manual unlock). Set False if you want every trip
            recorded.
        extra_reason_prefix: prepended to G9's reason in the audit row.
    """

    def _handler(order: PendingOrder, decisions: list) -> None:
        g9 = _find_deny(decisions, "consecutive_loss_cb")
        if g9 is None:
            return

        sid = order.strategy_id
        try:
            current = registry.get(sid)
        except StrategyNotFound:
            logger.warning(
                "g9 disabler: strategy %r not in registry — skipping",
                sid,
            )
            return

        if only_if_currently_enabled and not current.parsed.enabled:
            logger.debug(
                "g9 disabler: %r already disabled — skipping (idempotent)",
                sid,
            )
            return

        full_reason = f"{extra_reason_prefix} {g9.reason}".strip()
        try:
            registry.set_enabled(sid, False, reason=full_reason, actor=actor)
            logger.warning(
                "g9 auto-disabled strategy %r (actor=%s) — manual unlock "
                "required via `python -m strategy_engine.cli.admin enable %s "
                "--reason '...'`",
                sid, actor, sid,
            )
        except Exception as e:
            logger.exception(
                "g9 disabler: set_enabled(%r, False) failed — strategy "
                "still active in DB! (%s)", sid, e,
            )

    _handler.__name__ = "g9_strategy_disabler"
    return _handler


def _find_deny(decisions: list, guard_name: str):
    """Return the first DENY decision for `guard_name`, or None."""
    for d in decisions:
        gn = getattr(d, "guard_name", None)
        result = getattr(d, "result", None)
        if gn == guard_name and getattr(result, "value", None) == "deny":
            return d
    return None


__all__ = [
    "GuardSideEffectHandler",
    "chain_handlers",
    "make_g9_strategy_disabler",
]
