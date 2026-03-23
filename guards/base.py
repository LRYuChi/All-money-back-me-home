"""Guard base class for pre-execution risk checks (inspired by OpenAlice).

Guards are synchronous to avoid fragile asyncio.run_until_complete() calls
inside Freqtrade's event loop. Each guard's check() method runs in sequence.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GuardContext:
    """Context passed to each guard for evaluation."""

    symbol: str
    side: str  # "long" or "short"
    amount: float
    leverage: float
    account_balance: float
    open_positions: dict = field(default_factory=dict)  # symbol -> position info
    trade_history: list = field(default_factory=list)  # recent trades
    confidence: float = 0.5  # confidence engine score (0.0-1.0)


class Guard(ABC):
    """Base class for all risk control guards."""

    @abstractmethod
    def check(self, ctx: GuardContext) -> Optional[str]:
        """Check if the order should be allowed.

        Returns:
            None if the order passes, or a rejection reason string.
        """


@dataclass
class GuardLayer:
    """A named group of guards with an alert severity level.

    Layers enable early termination: if an account-level guard rejects,
    strategy-level and trade-level guards are skipped entirely.
    """

    name: str           # "account", "strategy", "trade"
    guards: list[Guard] = field(default_factory=list)
    alert_level: str = "info"  # "critical", "warning", "info"


class GuardPipeline:
    """Chains multiple guards (optionally grouped into layers) and runs them sequentially.

    Any single rejection aborts the order.
    Supports both flat guard lists (backward-compatible) and layered architecture.
    """

    def __init__(
        self,
        guards: list[Guard] | None = None,
        layers: list[GuardLayer] | None = None,
    ):
        self._layers = layers or []
        self._flat_guards = guards or []

    @property
    def guards(self) -> list[Guard]:
        """Flat list of all guards (backward-compatible for state persistence)."""
        if self._layers:
            return [g for layer in self._layers for g in layer.guards]
        return self._flat_guards

    @guards.setter
    def guards(self, value: list[Guard]) -> None:
        self._flat_guards = value
        self._layers = []

    def add(self, guard: Guard) -> None:
        self._flat_guards.append(guard)

    def run(self, ctx: GuardContext) -> Optional[str]:
        """Run all guards. Returns None if all pass, or the first rejection reason.

        When using layers, rejection at a higher layer skips all lower layers.
        The rejection message includes the layer name for alert routing.
        """
        if self._layers:
            return self._run_layered(ctx)
        return self._run_flat(ctx)

    def _run_flat(self, ctx: GuardContext) -> Optional[str]:
        for guard in self._flat_guards:
            reason = guard.check(ctx)
            if reason is not None:
                logger.warning("Guard %s rejected: %s", guard.__class__.__name__, reason)
                return f"[{guard.__class__.__name__}] {reason}"
        return None

    def _run_layered(self, ctx: GuardContext) -> Optional[str]:
        for layer in self._layers:
            for guard in layer.guards:
                reason = guard.check(ctx)
                if reason is not None:
                    logger.warning(
                        "Guard %s (layer=%s, alert=%s) rejected: %s",
                        guard.__class__.__name__, layer.name, layer.alert_level, reason,
                    )
                    return f"[L:{layer.name}] [{guard.__class__.__name__}] {reason}"
        return None

    @property
    def layers(self) -> list[GuardLayer]:
        return self._layers
