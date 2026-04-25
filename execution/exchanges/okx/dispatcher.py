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
    demo: bool | None = None,
    client: OKXClient | None = None,
):
    """Build an OKXLiveDispatcher if credentials are configured.

    Round 41: actually wires CcxtOKXClient when credentials present.
    Returns None when credentials are missing.

    Credential sources (priority):
      1. settings.okx_api_key/secret/passphrase (from env or .env)
      2. credential store (round 7+34) — if env values are blank

    Demo trading default: True for `mode=paper`, False for `mode=live`.
    Override via `demo=` kwarg. Demo flag toggles ccxt's sandbox endpoint
    + adds `x-simulated-trading: 1` header.

    `client=` injectable for tests so they don't hit the network.
    """
    api_key, secret, passphrase = _resolve_credentials(settings)
    if not (api_key and secret and passphrase):
        logger.info(
            "okx_dispatcher: credentials missing (api_key/secret/passphrase) "
            "— not registering live dispatcher"
        )
        return None

    if demo is None:
        # `paper` = simulated by definition; `live` = real money
        demo = (mode == "paper")

    if client is None:
        try:
            from execution.exchanges.okx.ccxt_client import CcxtOKXClient
            client = CcxtOKXClient(
                api_key=api_key, secret=secret, passphrase=passphrase,
                demo=demo,
            )
        except ImportError as e:
            logger.error(
                "okx_dispatcher: ccxt not importable (%s) — cannot wire live", e,
            )
            return None

    logger.info(
        "okx_dispatcher: wired (mode=%s, demo=%s, client=%s)",
        mode, demo, type(client).__name__,
    )
    return OKXLiveDispatcher(client, mode=mode)


def _resolve_credentials(settings) -> tuple[str, str, str]:  # noqa: ANN001
    """Try env first; fall back to encrypted credential store."""
    api_key = (getattr(settings, "okx_api_key", "") or "").strip()
    secret = (getattr(settings, "okx_api_secret", "") or "").strip()
    passphrase = (getattr(settings, "okx_api_passphrase", "") or "").strip()

    if api_key and secret and passphrase:
        return api_key, secret, passphrase

    # Fall through: ask credential store. Wrap in try so an absent /
    # misconfigured store doesn't crash the factory.
    try:
        from shared.credentials import build_store, with_actor
        store = build_store(settings)
        with with_actor("factory:okx_dispatcher"):
            api_key = api_key or store.read("OKX_API_KEY").plaintext
            secret = secret or store.read("OKX_API_SECRET").plaintext
            passphrase = passphrase or store.read("OKX_API_PASSPHRASE").plaintext
        return api_key, secret, passphrase
    except Exception as e:
        logger.debug(
            "okx_dispatcher: credential store fallback failed (%s)", e,
        )
        return api_key, secret, passphrase


__all__ = [
    "OKXLiveDispatcher",
    "build_okx_dispatcher",
]
