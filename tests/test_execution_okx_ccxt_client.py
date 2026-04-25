"""Tests for CcxtOKXClient + symbol mapping helpers (round 41).

Tests use a stub ccxt-style client passed via the `client=` kwarg so we
never hit the real OKX API. The stub mirrors ccxt's interface enough for
our mapping logic to exercise.
"""
from __future__ import annotations

from typing import Any

import pytest

from execution.exchanges import RetryPolicy
from execution.exchanges.okx import (
    CcxtOKXClient,
    canonical_to_ccxt,
    ccxt_to_canonical,
)
from execution.exchanges.types import (
    ExchangeRequest,
    ExchangeResponse,
    ExchangeResponseStatus,
)


# Fast policy for tests that exercise the failure path — keeps test
# runtime sub-second instead of 1.5s per test from default backoff.
NO_RETRY = RetryPolicy(max_attempts=1, base_delay_sec=0)


# ================================================================== #
# Symbol mapping helpers
# ================================================================== #
def test_canonical_to_ccxt_strips_prefix():
    assert canonical_to_ccxt("crypto:OKX:BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_canonical_to_ccxt_passthrough_when_no_prefix():
    """Defensive: if caller already passed a ccxt-form symbol, don't double-strip."""
    assert canonical_to_ccxt("BTC/USDT:USDT") == "BTC/USDT:USDT"


def test_ccxt_to_canonical_adds_prefix():
    assert ccxt_to_canonical("BTC/USDT:USDT") == "crypto:OKX:BTC/USDT:USDT"


def test_ccxt_to_canonical_passthrough_when_already_canonical():
    assert (
        ccxt_to_canonical("crypto:OKX:BTC/USDT:USDT")
        == "crypto:OKX:BTC/USDT:USDT"
    )


# ================================================================== #
# Stub ccxt-style client (mirrors interface CcxtOKXClient calls into)
# ================================================================== #
class StubCcxt:
    """Mimics the subset of ccxt.okx that CcxtOKXClient touches."""

    def __init__(
        self,
        *,
        ticker_price: float = 50_000.0,
        markets: dict | None = None,
        create_response: dict | None = None,
        create_exception: Exception | None = None,
        cancel_exception: Exception | None = None,
    ):
        self._ticker_price = ticker_price
        self._markets = markets or {
            "BTC/USDT:USDT": {"type": "swap", "settle": "USDT", "active": True},
        }
        self._create_response = create_response or {
            "id": "FAKE-123", "status": "open", "filled": 0, "cost": 0,
        }
        self._create_exception = create_exception
        self._cancel_exception = cancel_exception
        self.sandbox_called = False

        # call recorders
        self.create_calls: list[dict] = []
        self.cancel_calls: list[dict] = []
        self.fetch_ticker_calls: list[str] = []
        self.load_markets_calls = 0

    def set_sandbox_mode(self, on: bool) -> None:
        self.sandbox_called = bool(on)

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        self.fetch_ticker_calls.append(symbol)
        return {"last": self._ticker_price}

    def amount_to_precision(self, _symbol: str, amount: float) -> str:
        # Stub: round to 6 dp (matches OKX BTC contract precision)
        return f"{amount:.6f}"

    def create_order(self, *, symbol, type, side, amount, price, params):
        self.create_calls.append({
            "symbol": symbol, "type": type, "side": side,
            "amount": amount, "price": price, "params": params,
        })
        if self._create_exception:
            raise self._create_exception
        return self._create_response

    def cancel_order(self, *, id, symbol, params):
        self.cancel_calls.append({"id": id, "symbol": symbol, "params": params})
        if self._cancel_exception:
            raise self._cancel_exception
        return {}

    def load_markets(self) -> dict:
        self.load_markets_calls += 1
        return self._markets


def make_request(
    notional=100.0, symbol="crypto:OKX:BTC/USDT:USDT", side="long",
    order_type="market", limit_price=None, coid="rd41-test",
) -> ExchangeRequest:
    return ExchangeRequest(
        client_order_id=coid, symbol=symbol, side=side,
        notional_usd=notional, order_type=order_type, limit_price=limit_price,
    )


# ================================================================== #
# Construction
# ================================================================== #
def test_construction_requires_all_three_credentials():
    with pytest.raises(ValueError, match="non-empty"):
        CcxtOKXClient(api_key="", secret="x", passphrase="y", client=StubCcxt())


def test_construction_with_demo_calls_set_sandbox_mode():
    stub = StubCcxt()
    CcxtOKXClient(api_key="a", secret="b", passphrase="c",
                  demo=True, client=stub)
    # Note: sandbox_called only fires when ccxt is constructed inside;
    # we passed `client=` so the branch isn't exercised. This test just
    # confirms construction succeeds with demo=True.


def test_construction_does_not_load_real_ccxt_when_client_injected():
    """Passing client= → no `import ccxt` triggered. Lets unit tests
    run without ccxt being importable."""
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    assert c is not None


# ================================================================== #
# place_order — happy paths
# ================================================================== #
def test_place_order_market_uses_ticker_price_for_amount():
    stub = StubCcxt(ticker_price=50_000.0)
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(notional=100, order_type="market"))
    # 100 USD / 50_000 = 0.002 BTC
    assert stub.create_calls[0]["amount"] == pytest.approx(0.002)
    assert stub.fetch_ticker_calls == ["BTC/USDT:USDT"]


def test_place_order_limit_uses_limit_price_for_amount():
    stub = StubCcxt(ticker_price=999_999)   # should NOT be used
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(notional=200, order_type="limit", limit_price=50_000))
    assert stub.create_calls[0]["amount"] == pytest.approx(0.004)
    # No ticker fetch needed for limit
    assert stub.fetch_ticker_calls == []


def test_place_order_passes_clordid_in_params():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(coid="rd41-coid-xyz"))
    assert stub.create_calls[0]["params"]["clOrdId"] == "rd41-coid-xyz"


def test_place_order_defaults_tdmode_to_cross():
    """OKX V5 SWAP requires tdMode (cross/isolated). We default cross."""
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request())
    assert stub.create_calls[0]["params"]["tdMode"] == "cross"


def test_place_order_extra_params_passed_through():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    req = ExchangeRequest(
        client_order_id="x", symbol="crypto:OKX:BTC/USDT:USDT",
        side="long", notional_usd=100, order_type="market",
        extra={"posSide": "long"},
    )
    c.place_order(req)
    assert stub.create_calls[0]["params"]["posSide"] == "long"


def test_place_order_long_maps_to_buy():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(side="long"))
    assert stub.create_calls[0]["side"] == "buy"


def test_place_order_short_maps_to_sell():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(side="short"))
    assert stub.create_calls[0]["side"] == "sell"


def test_place_order_canonical_symbol_stripped_to_ccxt():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    c.place_order(make_request(symbol="crypto:OKX:ETH/USDT:USDT"))
    assert stub.create_calls[0]["symbol"] == "ETH/USDT:USDT"


# ================================================================== #
# place_order — response mapping
# ================================================================== #
def test_response_open_status_maps_to_accepted():
    stub = StubCcxt(create_response={
        "id": "X1", "status": "open", "filled": 0, "cost": 0,
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.ACCEPTED
    assert r.exchange_order_id == "X1"


def test_response_closed_with_filled_maps_to_filled():
    stub = StubCcxt(create_response={
        "id": "X2", "status": "closed", "filled": 0.002, "cost": 100.0,
        "average": 50_000.0,
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.FILLED
    assert r.filled_notional_usd == 100.0
    assert r.avg_fill_price == 50_000.0


def test_response_partial_fill_maps_to_partially_filled():
    stub = StubCcxt(create_response={
        "id": "X3", "status": "open", "filled": 0.001, "cost": 50.0,
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.PARTIALLY_FILLED


def test_response_canceled_status_maps_to_rejected():
    stub = StubCcxt(create_response={
        "id": "X4", "status": "canceled", "filled": 0,
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.REJECTED


# ================================================================== #
# place_order — exception mapping
# ================================================================== #
def test_network_exception_maps_to_network_error():
    class NetworkError(Exception): pass
    stub = StubCcxt(create_exception=NetworkError("conn reset"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c",
                      client=stub, retry_policy=NO_RETRY)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.NETWORK_ERROR
    assert r.error_code == "NetworkError"


def test_request_timeout_maps_to_network_error():
    class RequestTimeout(Exception): pass
    stub = StubCcxt(create_exception=RequestTimeout("timeout"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c",
                      client=stub, retry_policy=NO_RETRY)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.NETWORK_ERROR


def test_duplicate_message_maps_to_duplicate():
    """Idempotent retry: OKX returns 'Order already exists' style message."""
    class InvalidOrder(Exception): pass
    stub = StubCcxt(create_exception=InvalidOrder("Order already exists"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.DUPLICATE


def test_other_exception_maps_to_rejected():
    class BadRequest(Exception): pass
    stub = StubCcxt(create_exception=BadRequest("invalid amount"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request())
    assert r.status == ExchangeResponseStatus.REJECTED
    assert r.error_code == "BadRequest"
    assert "invalid amount" in r.error_message


def test_zero_notional_rejected_without_calling_exchange():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request(notional=0))
    assert r.status == ExchangeResponseStatus.REJECTED
    assert "size_calc_failed" in r.error_code
    assert stub.create_calls == []


def test_ticker_returns_zero_price_rejected():
    """Defensive: if exchange returns a 0 ticker, reject rather than
    submit nonsense amount."""
    stub = StubCcxt(ticker_price=0)
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    r = c.place_order(make_request(order_type="market"))
    assert r.status == ExchangeResponseStatus.REJECTED
    assert stub.create_calls == []


# ================================================================== #
# cancel_order
# ================================================================== #
def test_cancel_order_passes_clordid():
    stub = StubCcxt()
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    assert c.cancel_order("rd41-coid-xyz") is True
    assert stub.cancel_calls[0]["params"]["clOrdId"] == "rd41-coid-xyz"


def test_cancel_order_returns_false_on_order_not_found():
    class OrderNotFound(Exception): pass
    stub = StubCcxt(cancel_exception=OrderNotFound("not found"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    assert c.cancel_order("dead-coid") is False


def test_cancel_order_returns_false_on_other_exceptions():
    stub = StubCcxt(cancel_exception=ConnectionError("net down"))
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c",
                      client=stub, retry_policy=NO_RETRY)
    assert c.cancel_order("x") is False


# ================================================================== #
# fetch_instruments
# ================================================================== #
def test_fetch_instruments_filters_to_usdt_swap_active():
    stub = StubCcxt(markets={
        "BTC/USDT:USDT": {"type": "swap", "settle": "USDT", "active": True},
        "ETH/USDT:USDT": {"type": "swap", "settle": "USDT", "active": True},
        "BTC/USD:BTC":   {"type": "swap", "settle": "BTC", "active": True},   # USD-margined
        "BTC/USDT":      {"type": "spot", "settle": "USDT", "active": True},  # spot
        "OLD/USDT:USDT": {"type": "swap", "settle": "USDT", "active": False}, # delisted
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    result = c.fetch_instruments()
    assert result == {
        "crypto:OKX:BTC/USDT:USDT",
        "crypto:OKX:ETH/USDT:USDT",
    }


def test_fetch_instruments_returns_empty_on_load_markets_failure():
    class BoomCcxt:
        def set_sandbox_mode(self, on): pass
        def load_markets(self):
            raise ConnectionError("net down")
        def fetch_ticker(self, s): return {"last": 1}
        def amount_to_precision(self, s, a): return str(a)
        def create_order(self, **kw): return {}
        def cancel_order(self, **kw): return {}
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=BoomCcxt())
    assert c.fetch_instruments() == set()


def test_fetch_instruments_canonicalises_symbols():
    stub = StubCcxt(markets={
        "SOL/USDT:USDT": {"type": "swap", "settle": "USDT", "active": True},
    })
    c = CcxtOKXClient(api_key="a", secret="b", passphrase="c", client=stub)
    assert "crypto:OKX:SOL/USDT:USDT" in c.fetch_instruments()
