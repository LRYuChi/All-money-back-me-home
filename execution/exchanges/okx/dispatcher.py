"""OKXLiveDispatcher — implements execution.pending_orders.Dispatcher.

Round 32 wiring:
  PendingOrder ──▶ ExchangeRequest (deterministic client_order_id)
              ──▶ OKXClient.place_order(req)
              ──▶ ExchangeResponse
              ──▶ DispatchResult (status mapping below)

Status mapping (ExchangeResponseStatus → PendingOrderStatus):
  ACCEPTED            → SUBMITTED          (worker polls for fill later — F.1.x)
  FILLED              → FILLED
  PARTIALLY_FILLED    → PARTIALLY_FILLED   (non-terminal, worker handles)
  REJECTED            → REJECTED
  DUPLICATE           → SUBMITTED          (idempotent retry; treat as in-flight)
  NETWORK_ERROR       → REJECTED + retryable error string

The worker doesn't auto-retry on NETWORK_ERROR — strategy will fire again
next tick and we'll re-enqueue (with the same client_order_id, so OKX
dedupes if the first attempt actually landed).
"""
from __future__ import annotations

import logging
from typing import Any, Literal

from execution.exchanges.idempotency import make_client_order_id
from execution.exchanges.okx.client import OKXClient
from execution.exchanges.types import (
    ExchangeRequest,
    ExchangeResponse,
    ExchangeResponseStatus,
)
from execution.pending_orders.types import (
    ExecutionMode,
    PendingOrder,
    PendingOrderStatus,
)
from execution.pending_orders.worker import DispatchResult

logger = logging.getLogger(__name__)


class OKXLiveDispatcher:
    """Live OKX dispatcher. Dispatcher Protocol-compliant."""

    def __init__(
        self,
        client: OKXClient,
        *,
        mode: ExecutionMode = "live",
        order_type: Literal["market", "limit"] = "market",
    ):
        if mode not in ("live", "paper"):
            raise ValueError(
                f"OKXLiveDispatcher mode must be 'live' or 'paper'; "
                f"got {mode!r}"
            )
        self._client = client
        self._mode = mode
        self._order_type = order_type

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    def dispatch(self, order: PendingOrder) -> DispatchResult:
        try:
            req = self._build_request(order)
        except ValueError as e:
            logger.warning(
                "okx dispatcher: cannot build request for order id=%s: %s",
                order.id, e,
            )
            return DispatchResult(
                terminal_status=PendingOrderStatus.REJECTED,
                last_error=f"build_request: {e}",
            )

        try:
            resp = self._client.place_order(req)
        except Exception as e:
            logger.exception(
                "okx dispatcher: place_order raised on id=%s: %s",
                order.id, e,
            )
            return DispatchResult(
                terminal_status=PendingOrderStatus.REJECTED,
                last_error=f"network_error: {type(e).__name__}: {e}",
            )

        return self._map_response(req, resp)

    # ---------------------------------------------------------------- #
    def _build_request(self, order: PendingOrder) -> ExchangeRequest:
        if order.target_notional_usd <= 0:
            raise ValueError(
                f"target_notional_usd must be > 0; got {order.target_notional_usd}"
            )
        # client_order_id seeded from intent ts (created_at on the row).
        # Re-dispatch of the same row → same coid → exchange dedupes.
        coid = make_client_order_id(
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            side=order.side,
            intent_ts=order.created_at,
            mode=self._mode,
        )
        return ExchangeRequest(
            client_order_id=coid,
            symbol=order.symbol,
            side=order.side,
            notional_usd=order.target_notional_usd,
            order_type=self._order_type,
            limit_price=order.entry_price_ref if self._order_type == "limit" else None,
        )

    def _map_response(
        self, req: ExchangeRequest, resp: ExchangeResponse,
    ) -> DispatchResult:
        s = resp.status
        detail: dict[str, Any] = {
            "dispatcher": "okx_live",
            "client_order_id": req.client_order_id,
            "exchange_order_id": resp.exchange_order_id,
            "status": s.value,
        }

        if s == ExchangeResponseStatus.FILLED:
            return DispatchResult(
                terminal_status=PendingOrderStatus.FILLED,
                detail={**detail, "fill_notional": resp.filled_notional_usd},
            )
        if s == ExchangeResponseStatus.PARTIALLY_FILLED:
            return DispatchResult(
                terminal_status=PendingOrderStatus.PARTIALLY_FILLED,
                detail={**detail, "fill_notional": resp.filled_notional_usd},
            )
        if s in (
            ExchangeResponseStatus.ACCEPTED,
            ExchangeResponseStatus.DUPLICATE,
        ):
            return DispatchResult(
                terminal_status=PendingOrderStatus.SUBMITTED,
                detail=detail,
            )
        if s == ExchangeResponseStatus.REJECTED:
            return DispatchResult(
                terminal_status=PendingOrderStatus.REJECTED,
                last_error=(
                    f"okx_rejected[{resp.error_code or '?'}]: "
                    f"{resp.error_message or 'unknown'}"
                ),
                detail=detail,
            )
        if s == ExchangeResponseStatus.NETWORK_ERROR:
            return DispatchResult(
                terminal_status=PendingOrderStatus.REJECTED,
                last_error=(
                    f"okx_network: {resp.error_message or 'transport failure'}"
                ),
                detail=detail,
            )

        # Defensive — shouldn't reach here unless a new enum value was added
        return DispatchResult(
            terminal_status=PendingOrderStatus.REJECTED,
            last_error=f"okx_unknown_status: {s.value}",
            detail=detail,
        )


# ================================================================== #
# Factory
# ================================================================== #
def build_okx_dispatcher(
    settings,                          # noqa: ANN001
    *,
    mode: ExecutionMode = "live",
):
    """Build an OKXLiveDispatcher if credentials are configured.

    Returns None when okx_api_key/secret/passphrase are missing — caller
    (DispatcherRegistry.build_default_registry) skips registration so
    `--mode live` exits 1 instead of attempting unauthenticated requests.

    Round 32 doesn't import a real ccxt client — it raises NotImplementedError
    when called for `live`. Tests + a future F.1.x round inject a concrete
    client. The factory still returns a working dispatcher when the caller
    passes `client=` directly (used by build_default_registry below once
    the real ccxt wiring lands).
    """
    api_key = (getattr(settings, "okx_api_key", "") or "").strip()
    secret = (getattr(settings, "okx_api_secret", "") or "").strip()
    passphrase = (getattr(settings, "okx_api_passphrase", "") or "").strip()
    if not (api_key and secret and passphrase):
        logger.info(
            "okx_dispatcher: credentials missing (api_key/secret/passphrase) "
            "— not registering live dispatcher"
        )
        return None

    # Round 32 placeholder — real ccxt-okx wiring lands in F.1.x.
    raise NotImplementedError(
        "OKX live dispatcher requires F.1.x ccxt wiring; round 32 ships only "
        "the scaffolding (Protocol + Fake). Construct OKXLiveDispatcher "
        "directly with a concrete OKXClient when ready."
    )


__all__ = [
    "OKXLiveDispatcher",
    "build_okx_dispatcher",
]
