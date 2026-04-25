"""OKX HTTP client Protocol + an in-memory FakeOKXClient for tests.

Round 32 ships the Protocol + Fake. The real ccxt-based client lands
in F.1.x once sandbox-tested; it'll just need to implement the same
3-method interface.

Protocol surface is intentionally narrow — what the Dispatcher needs,
nothing more:
  - place_order(req)        → ExchangeResponse
  - cancel_order(coid)      → bool
  - fetch_instruments()     → set[str]   (canonical symbols)

Real-client implementation notes (for F.1.x):
  - Use ccxt.okx (CCXT bundle) — supports unified API, async, idempotent
  - place_order maps to v5 /api/v5/trade/order  with clOrdId=client_order_id
  - cancel_order maps to v5 /api/v5/trade/cancel-order
  - fetch_instruments maps to v5 /api/v5/public/instruments?instType=SWAP
    + filter to USDT-quoted perps
"""
from __future__ import annotations

import logging
from typing import Protocol

from execution.exchanges.types import (
    ExchangeRequest,
    ExchangeResponse,
    ExchangeResponseStatus,
)

logger = logging.getLogger(__name__)


class OKXClient(Protocol):
    """All concrete OKX clients implement this 4-method interface."""

    def place_order(self, request: ExchangeRequest) -> ExchangeResponse: ...
    def cancel_order(self, client_order_id: str) -> bool: ...
    def fetch_instruments(self) -> set[str]: ...
    def fetch_order(
        self, client_order_id: str, symbol: str,
    ) -> ExchangeResponse: ...


# ================================================================== #
# FakeOKXClient — for unit tests + smoke
# ================================================================== #
class FakeOKXClient:
    """In-memory client. Tests pre-program the responses; the dispatcher
    treats it identically to the real one."""

    def __init__(
        self,
        *,
        instruments: set[str] | None = None,
        place_response: ExchangeResponse | None = None,
        place_responses: list[ExchangeResponse] | None = None,
        cancel_result: bool = True,
        fetch_responses: dict[str, ExchangeResponse] | None = None,
    ):
        self._instruments = set(instruments or ())
        self._place_response = place_response
        self._place_responses = list(place_responses or [])
        self._cancel_result = cancel_result
        # Map client_order_id → response to return from fetch_order
        self._fetch_responses: dict[str, ExchangeResponse] = dict(
            fetch_responses or {}
        )

        # Recorded calls (tests assert against these)
        self.place_calls: list[ExchangeRequest] = []
        self.cancel_calls: list[str] = []
        self.fetch_calls: int = 0
        self.fetch_order_calls: list[tuple[str, str]] = []

        # Idempotency map: client_order_id → first response we returned
        self._seen_coids: dict[str, ExchangeResponse] = {}

    def place_order(self, request: ExchangeRequest) -> ExchangeResponse:
        self.place_calls.append(request)

        # Real OKX returns DUPLICATE on a known clOrdId — model that
        if request.client_order_id in self._seen_coids:
            prior = self._seen_coids[request.client_order_id]
            return ExchangeResponse(
                status=ExchangeResponseStatus.DUPLICATE,
                exchange_order_id=prior.exchange_order_id,
                error_message="client_order_id already exists",
                raw={"prior_status": prior.status.value},
            )

        if self._place_responses:
            resp = self._place_responses.pop(0)
        elif self._place_response is not None:
            resp = self._place_response
        else:
            # Default happy-path: ACCEPTED with deterministic ex order id
            resp = ExchangeResponse(
                status=ExchangeResponseStatus.ACCEPTED,
                exchange_order_id=f"FAKE-{request.client_order_id}",
            )

        self._seen_coids[request.client_order_id] = resp
        return resp

    def cancel_order(self, client_order_id: str) -> bool:
        self.cancel_calls.append(client_order_id)
        return self._cancel_result

    def fetch_instruments(self) -> set[str]:
        self.fetch_calls += 1
        return set(self._instruments)

    def fetch_order(
        self, client_order_id: str, symbol: str,
    ) -> ExchangeResponse:
        """Test stub: return programmed response for the coid, or fall
        back to the last place_order response so happy-path tests work
        without explicitly seeding fetch_responses."""
        self.fetch_order_calls.append((client_order_id, symbol))
        if client_order_id in self._fetch_responses:
            return self._fetch_responses[client_order_id]
        if client_order_id in self._seen_coids:
            return self._seen_coids[client_order_id]
        return ExchangeResponse(
            status=ExchangeResponseStatus.REJECTED,
            error_code="not_found",
            error_message=f"no record of {client_order_id}",
        )


__all__ = ["OKXClient", "FakeOKXClient"]
