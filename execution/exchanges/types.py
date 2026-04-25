"""Exchange-agnostic request/response types.

Concrete adapters translate to/from these. Keeps the Dispatcher contract
narrow: a Dispatcher takes a PendingOrder, builds an ExchangeRequest,
asks its Client to send, and converts the ExchangeResponse to a
DispatchResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


OrderType = Literal["market", "limit"]
TimeInForce = Literal["gtc", "ioc", "fok"]   # good-till-cancelled, IOC, FOK


@dataclass(slots=True, frozen=True)
class ExchangeRequest:
    """The normalised "place order" request submitted to a Client.

    `client_order_id` MUST be deterministic per logical intent — see
    `idempotency.make_client_order_id`. Re-submitting the same order
    (e.g. on worker restart between dispatch and ack) MUST produce the
    same id so the exchange dedupes server-side.
    """

    client_order_id: str
    symbol: str                            # canonical, e.g. "crypto:OKX:BTC/USDT:USDT"
    side: Literal["long", "short"]
    notional_usd: float
    order_type: OrderType = "market"
    limit_price: float | None = None       # required if order_type=limit
    time_in_force: TimeInForce = "ioc"     # market default; limit can be GTC

    # Optional: exchange-specific fields (passed through verbatim)
    extra: dict = field(default_factory=dict)


class ExchangeResponseStatus(str, Enum):
    """Discrete outcomes from a Client.place_order call."""

    ACCEPTED = "accepted"            # order on book / submitted
    FILLED = "filled"                # fully filled immediately
    PARTIALLY_FILLED = "partially_filled"
    REJECTED = "rejected"            # exchange refused (validation, balance...)
    DUPLICATE = "duplicate"          # client_order_id already submitted (idempotent)
    NETWORK_ERROR = "network_error"  # transport-level — caller decides retry policy


@dataclass(slots=True, frozen=True)
class ExchangeResponse:
    """Normalised response. `exchange_order_id` is set when the exchange
    accepted (or already had) the order; None on REJECTED/NETWORK_ERROR.
    `error_code` is the exchange's machine-readable code (e.g. OKX 51000
    series); `error_message` is the human text."""

    status: ExchangeResponseStatus
    exchange_order_id: str | None = None
    filled_notional_usd: float = 0.0
    avg_fill_price: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw: dict = field(default_factory=dict)


class ExchangeError(Exception):
    """Base exception for exchange-layer failures. Concrete adapters
    may subclass; callers typically catch ExchangeError + retry-or-fail."""

    def __init__(self, message: str, *, code: str | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


__all__ = [
    "ExchangeError",
    "ExchangeRequest",
    "ExchangeResponse",
    "ExchangeResponseStatus",
    "OrderType",
    "TimeInForce",
]
