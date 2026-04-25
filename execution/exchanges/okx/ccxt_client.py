"""CcxtOKXClient — concrete OKXClient using ccxt-okx (round 41).

Replaces the round-32 NotImplementedError in build_okx_dispatcher when
credentials are present + ccxt is importable.

Design notes:
  - ccxt's `set_sandbox_mode(True)` switches OKX endpoints AND injects
    `x-simulated-trading: 1` header → demo trading auto-handled
  - Symbol canonicalisation: "crypto:OKX:BTC/USDT:USDT" ↔ ccxt
    "BTC/USDT:USDT" (USDT-margined perpetual SWAP convention)
  - Notional → amount conversion: market orders fetch ticker mid-price,
    limit orders divide by limit_price. Tick-size rounding is left to
    ccxt's `amount_to_precision` so we don't have to ship a quoter.
  - Idempotency: ExchangeRequest.client_order_id passed via OKX's
    `clOrdId` param. Re-submitting the same coid → OKX returns the
    existing order's state (mapped to DUPLICATE in our response).

Failure mapping:
  - ccxt.NetworkError / ccxt.RequestTimeout → NETWORK_ERROR
  - ccxt.OrderNotFound (on cancel) → success=False but no exception
  - ccxt.InvalidOrder / BadRequest / InsufficientFunds → REJECTED
  - Other ccxt.ExchangeError → REJECTED with the upstream message
  - Anything else → re-raised (the dispatcher catches at the top level)
"""
from __future__ import annotations

import logging
from typing import Any

from execution.exchanges.okx.client import OKXClient
from execution.exchanges.types import (
    ExchangeRequest,
    ExchangeResponse,
    ExchangeResponseStatus,
)

logger = logging.getLogger(__name__)


# Canonical symbols look like "crypto:OKX:BTC/USDT:USDT" (asset_class +
# venue + base/quote:settle). ccxt uses just "BTC/USDT:USDT".
_CANONICAL_PREFIX = "crypto:OKX:"


def canonical_to_ccxt(symbol: str) -> str:
    """'crypto:OKX:BTC/USDT:USDT' → 'BTC/USDT:USDT'."""
    if symbol.startswith(_CANONICAL_PREFIX):
        return symbol[len(_CANONICAL_PREFIX):]
    return symbol


def ccxt_to_canonical(symbol: str) -> str:
    """'BTC/USDT:USDT' → 'crypto:OKX:BTC/USDT:USDT'."""
    if symbol.startswith(_CANONICAL_PREFIX):
        return symbol
    return _CANONICAL_PREFIX + symbol


class CcxtOKXClient:
    """OKXClient backed by ccxt.okx. See module docstring for design."""

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        demo: bool = True,
        client: Any = None,            # injectable for tests
    ):
        if not (api_key and secret and passphrase):
            raise ValueError(
                "CcxtOKXClient requires non-empty api_key, secret, passphrase"
            )
        if client is not None:
            self._exchange = client
        else:
            import ccxt   # lazy import to keep tests fast when not used
            self._exchange = ccxt.okx({
                "apiKey": api_key,
                "secret": secret,
                "password": passphrase,
                "enableRateLimit": True,
            })
            if demo:
                # ccxt-okx's sandbox flag swaps endpoint + adds
                # x-simulated-trading: 1 header automatically
                self._exchange.set_sandbox_mode(True)
                logger.info("ccxt-okx: sandbox/demo mode enabled")

    # ---------------------------------------------------------------- #
    # OKXClient Protocol implementation
    # ---------------------------------------------------------------- #
    def place_order(self, request: ExchangeRequest) -> ExchangeResponse:
        ccxt_symbol = canonical_to_ccxt(request.symbol)
        ccxt_side = "buy" if request.side == "long" else "sell"
        ccxt_type = request.order_type   # "market" or "limit" align with ccxt

        try:
            amount = self._notional_to_amount(
                ccxt_symbol, request.notional_usd, request.limit_price,
            )
        except Exception as e:
            return ExchangeResponse(
                status=ExchangeResponseStatus.REJECTED,
                error_code="size_calc_failed",
                error_message=f"{type(e).__name__}: {e}",
            )

        params: dict[str, Any] = {"clOrdId": request.client_order_id}
        # OKX V5 SWAP requires `tdMode` (cross/isolated). Default cross —
        # safer for retail demo accounts. Caller can override via extra.
        params.setdefault("tdMode", "cross")
        params.update(request.extra)

        try:
            raw = self._exchange.create_order(
                symbol=ccxt_symbol,
                type=ccxt_type,
                side=ccxt_side,
                amount=amount,
                price=request.limit_price,
                params=params,
            )
        except Exception as e:
            return self._map_exception(e)

        return self._map_create_response(raw)

    def cancel_order(self, client_order_id: str) -> bool:
        try:
            self._exchange.cancel_order(
                id=None, symbol=None,
                params={"clOrdId": client_order_id},
            )
            return True
        except Exception as e:
            # OrderNotFound is normal (already filled / cancelled / typo);
            # reflect that as False, log everything else as warning.
            name = type(e).__name__
            if name == "OrderNotFound":
                logger.info("cancel_order: %s not found (likely already terminal)",
                            client_order_id)
            else:
                logger.warning("cancel_order failed for %s: %s",
                               client_order_id, e)
            return False

    def fetch_order(
        self, client_order_id: str, symbol: str,
    ) -> ExchangeResponse:
        """Round 42: poll an open order's current state by clOrdId.

        OKX V5 lookup uses `clOrdId` in params (id arg unused for ccxt-okx).
        The returned ccxt order dict goes through the same status mapping
        as place_order — so callers get a uniform ExchangeResponse.
        """
        ccxt_symbol = canonical_to_ccxt(symbol)
        try:
            raw = self._exchange.fetch_order(
                id="",     # ccxt requires positional but okx uses clOrdId
                symbol=ccxt_symbol,
                params={"clOrdId": client_order_id},
            )
        except Exception as e:
            name = type(e).__name__
            if name in ("OrderNotFound",):
                # Order disappeared (cancelled out-of-band, history pruned)
                # Treat as REJECTED so worker stops polling it.
                return ExchangeResponse(
                    status=ExchangeResponseStatus.REJECTED,
                    error_code="OrderNotFound",
                    error_message=str(e),
                )
            return self._map_exception(e)
        return self._map_create_response(raw)

    def fetch_instruments(self) -> set[str]:
        """Returns the set of canonical symbols the exchange will accept.
        Filters to USDT-margined SWAP perpetuals (the universe we trade)."""
        try:
            markets = self._exchange.load_markets()
        except Exception as e:
            logger.warning("fetch_instruments: load_markets failed (%s)", e)
            return set()

        out: set[str] = set()
        for sym, meta in markets.items():
            # OKX SWAP USDT-margined perps: type='swap', settle='USDT', linear
            if meta.get("type") != "swap":
                continue
            if meta.get("settle") != "USDT":
                continue
            if not meta.get("active", True):
                continue
            out.add(ccxt_to_canonical(sym))
        return out

    # ---------------------------------------------------------------- #
    # Helpers
    # ---------------------------------------------------------------- #
    def _notional_to_amount(
        self, ccxt_symbol: str, notional_usd: float, limit_price: float | None,
    ) -> float:
        """Convert USD notional to ccxt's base-currency amount.

        Limit order: divide by limit_price.
        Market order: fetch ticker mid-price.
        ccxt's amount_to_precision rounds to the exchange's tick.
        """
        if notional_usd <= 0:
            raise ValueError(f"notional_usd must be > 0; got {notional_usd}")

        if limit_price is not None and limit_price > 0:
            price = float(limit_price)
        else:
            ticker = self._exchange.fetch_ticker(ccxt_symbol)
            price = float(ticker.get("last") or ticker.get("close") or 0)
            if price <= 0:
                raise RuntimeError(
                    f"fetch_ticker for {ccxt_symbol} returned no usable price"
                )

        raw_amount = notional_usd / price
        try:
            return float(self._exchange.amount_to_precision(ccxt_symbol, raw_amount))
        except Exception:
            # Fallback if precision tables aren't loaded — still return
            # the raw value and let the exchange complain if invalid
            return raw_amount

    def _map_create_response(self, raw: dict) -> ExchangeResponse:
        """ccxt's response → ExchangeResponse."""
        ex_id = raw.get("id")
        status = (raw.get("status") or "").lower()
        filled = float(raw.get("filled") or 0)
        cost = float(raw.get("cost") or 0)
        price_avg = raw.get("average") or raw.get("price")

        if status == "closed" and filled > 0:
            mapped = ExchangeResponseStatus.FILLED
        elif filled > 0:
            mapped = ExchangeResponseStatus.PARTIALLY_FILLED
        elif status in ("open", "pending"):
            mapped = ExchangeResponseStatus.ACCEPTED
        elif status in ("canceled", "cancelled", "rejected", "expired"):
            mapped = ExchangeResponseStatus.REJECTED
        else:
            # Unknown — be conservative and treat as ACCEPTED so worker polls
            mapped = ExchangeResponseStatus.ACCEPTED

        return ExchangeResponse(
            status=mapped,
            exchange_order_id=str(ex_id) if ex_id is not None else None,
            filled_notional_usd=cost if cost > 0 else 0.0,
            avg_fill_price=float(price_avg) if price_avg is not None else None,
            raw=raw,
        )

    def _map_exception(self, e: Exception) -> ExchangeResponse:
        """ccxt exception → ExchangeResponse. Imports ccxt lazily so tests
        that monkey-patch ccxt out still pass."""
        name = type(e).__name__
        msg = str(e)
        # Idempotency: OKX's "Order already exists" comes back as
        # InvalidOrder / DuplicateOrderId in different ccxt versions.
        if "duplicate" in msg.lower() or "already exists" in msg.lower():
            return ExchangeResponse(
                status=ExchangeResponseStatus.DUPLICATE,
                error_code="duplicate", error_message=msg,
            )
        # Network / timeout → retryable
        if name in ("NetworkError", "RequestTimeout", "DDoSProtection",
                    "ConnectionError", "TimeoutError"):
            return ExchangeResponse(
                status=ExchangeResponseStatus.NETWORK_ERROR,
                error_code=name, error_message=msg,
            )
        # Everything else maps to REJECTED with upstream code/message
        return ExchangeResponse(
            status=ExchangeResponseStatus.REJECTED,
            error_code=name, error_message=msg,
        )


__all__ = [
    "CcxtOKXClient",
    "canonical_to_ccxt",
    "ccxt_to_canonical",
]
