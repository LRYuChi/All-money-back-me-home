"""Guard framework — Protocol + GuardDecision + Pipeline orchestrator.

A Guard inspects a `PendingOrder` against a `GuardContext` (live
exposure / capital / signal age / etc.) and returns one of three
verdicts:
  - ALLOW   — order proceeds unchanged
  - SCALE   — order proceeds with reduced target_notional_usd
  - DENY    — order rejected with reason

Pipeline runs guards in declared order. First DENY short-circuits.
Scale verdicts mutate the order so subsequent guards see the reduced
size — this matters when e.g. PerStrategy scales down to 80%, then
PerMarket may still need to scale further. Order matters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from execution.pending_orders.types import PendingOrder


class GuardResult(str, Enum):
    ALLOW = "allow"
    SCALE = "scale"
    DENY = "deny"


@dataclass(slots=True, frozen=True)
class GuardDecision:
    """One guard's verdict.

    `scaled_size_usd` REQUIRED when result=SCALE; ignored otherwise.
    `reason` is human-readable; `detail` carries structured audit data.
    """

    guard_name: str
    result: GuardResult
    reason: str = ""
    scaled_size_usd: float | None = None
    detail: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.result != GuardResult.DENY


@dataclass(slots=True, frozen=True)
class GuardContext:
    """All the live state a guard might need. Caller (worker or
    pipeline-aware daemon) populates from accounting tables / signal
    metadata.

    Defaults are conservative: 0 capital + empty positions means every
    exposure guard will refuse to scale up — caller MUST populate."""

    capital_usd: float
    open_notional_by_strategy: dict[str, float] = field(default_factory=dict)
    open_notional_by_market: dict[str, float] = field(default_factory=dict)
    global_open_notional: float = 0.0
    signal_age_seconds: float | None = None     # for LatencyBudgetGuard


class Guard(Protocol):
    """All guards implement check(). `name` is used in DENY/SCALE audit
    rows; keep it short + grep-friendly (e.g. 'latency', 'min_size')."""

    name: str

    def check(
        self, order: PendingOrder, ctx: GuardContext,
    ) -> GuardDecision: ...


@dataclass(slots=True)
class PipelineRun:
    """Result of a full pipeline run."""

    decisions: list[GuardDecision]
    final_order: PendingOrder       # may have been mutated by SCALE guards
    accepted: bool                  # False if any DENY occurred
    final_notional_usd: float       # ending size (may differ from input)


class GuardPipeline:
    """Composes guards in fixed order. Stateless — caller injects
    GuardContext per evaluation.

    Mutation policy: `evaluate()` mutates `order.target_notional_usd` if
    a SCALE guard fires. To keep the input intact, pass a copy via
    `dataclasses.replace(order, ...)` or use `evaluate_copy()` (returns
    a fresh PendingOrder, doesn't touch input)."""

    def __init__(self, guards: list[Guard]) -> None:
        self._guards = list(guards)

    @property
    def guards(self) -> list[Guard]:
        return list(self._guards)

    def evaluate(
        self, order: PendingOrder, ctx: GuardContext,
    ) -> PipelineRun:
        decisions: list[GuardDecision] = []
        for g in self._guards:
            d = g.check(order, ctx)
            decisions.append(d)

            if d.result == GuardResult.DENY:
                return PipelineRun(
                    decisions=decisions, final_order=order,
                    accepted=False, final_notional_usd=order.target_notional_usd,
                )

            if d.result == GuardResult.SCALE:
                if d.scaled_size_usd is None or d.scaled_size_usd <= 0:
                    # Misbehaving guard — treat as DENY for safety
                    return PipelineRun(
                        decisions=decisions, final_order=order,
                        accepted=False,
                        final_notional_usd=order.target_notional_usd,
                    )
                order.target_notional_usd = d.scaled_size_usd

        return PipelineRun(
            decisions=decisions, final_order=order,
            accepted=True, final_notional_usd=order.target_notional_usd,
        )


__all__ = [
    "Guard",
    "GuardContext",
    "GuardDecision",
    "GuardPipeline",
    "GuardResult",
    "PipelineRun",
]
