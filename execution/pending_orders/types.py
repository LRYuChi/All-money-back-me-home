"""Pending order dataclass + status enum."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Literal


OrderSide = Literal["long", "short"]
ExecutionMode = Literal["shadow", "paper", "live", "notify"]


class PendingOrderStatus(str, Enum):
    """State machine for a pending order. Transitions:

        pending     → dispatching → submitted → filled
                                              ↘ partially_filled
                                              ↘ rejected
        pending     → cancelled               (human / CB)
        pending     → expired                 (idle too long)
        submitted   → cancelled               (open order cancelled)

    `pending` is the initial state; `filled`/`rejected`/`cancelled`/
    `expired` are terminal. `partially_filled` is non-terminal — worker
    can decide to cancel remainder or wait.
    """

    PENDING = "pending"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# Set of statuses that are terminal — workers skip these on poll.
TERMINAL_STATUSES: set[PendingOrderStatus] = {
    PendingOrderStatus.FILLED,
    PendingOrderStatus.REJECTED,
    PendingOrderStatus.CANCELLED,
    PendingOrderStatus.EXPIRED,
}


@dataclass(slots=True)
class PendingOrder:
    """One row of pending_orders. Mutable for status transitions —
    worker updates `status` + `attempts` + `last_error` over its lifetime."""

    strategy_id: str
    symbol: str
    side: OrderSide
    target_notional_usd: float
    mode: ExecutionMode
    entry_price_ref: float | None = None
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    status: PendingOrderStatus = PendingOrderStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    fused_signal_id: int | None = None
    client_order_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    dispatched_at: datetime | None = None
    completed_at: datetime | None = None
    id: int | None = None        # set by queue on insert

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_row(self) -> dict:
        """Serialise for DB insert/update."""
        return {
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side,
            "target_notional_usd": self.target_notional_usd,
            "entry_price_ref": self.entry_price_ref,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "mode": self.mode,
            "status": self.status.value,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "fused_signal_id": self.fused_signal_id,
            "client_order_id": self.client_order_id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
            "updated_at": self.updated_at.astimezone(timezone.utc).isoformat(),
            "dispatched_at": self.dispatched_at.astimezone(timezone.utc).isoformat() if self.dispatched_at else None,
            "completed_at": self.completed_at.astimezone(timezone.utc).isoformat() if self.completed_at else None,
        }


__all__ = [
    "OrderSide",
    "ExecutionMode",
    "PendingOrderStatus",
    "TERMINAL_STATUSES",
    "PendingOrder",
]
