"""Guard base class for pre-execution risk checks (inspired by OpenAlice)."""

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


class Guard(ABC):
    """Base class for all risk control guards."""

    @abstractmethod
    async def check(self, ctx: GuardContext) -> Optional[str]:
        """Check if the order should be allowed.

        Returns:
            None if the order passes, or a rejection reason string.
        """


class GuardPipeline:
    """Chains multiple guards and runs them sequentially.

    Any single rejection aborts the order.
    """

    def __init__(self, guards: list[Guard] | None = None):
        self.guards = guards or []

    def add(self, guard: Guard) -> None:
        self.guards.append(guard)

    async def run(self, ctx: GuardContext) -> Optional[str]:
        """Run all guards. Returns None if all pass, or the first rejection reason."""
        for guard in self.guards:
            reason = await guard.check(ctx)
            if reason is not None:
                logger.warning("Guard %s rejected: %s", guard.__class__.__name__, reason)
                return f"[{guard.__class__.__name__}] {reason}"
        return None
