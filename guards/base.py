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


class GuardPipeline:
    """Chains multiple guards and runs them sequentially.

    Any single rejection aborts the order.
    """

    def __init__(self, guards: list[Guard] | None = None):
        self.guards = guards or []

    def add(self, guard: Guard) -> None:
        self.guards.append(guard)

    def run(self, ctx: GuardContext) -> Optional[str]:
        """Run all guards. Returns None if all pass, or the first rejection reason."""
        for guard in self.guards:
            reason = guard.check(ctx)
            if reason is not None:
                logger.warning("Guard %s rejected: %s", guard.__class__.__name__, reason)
                return f"[{guard.__class__.__name__}] {reason}"
        return None
