"""Tests for round 32: OKX adapter scaffolding + G2 SymbolSupportedGuard."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from execution.exchanges import (
    InMemorySymbolCatalog,
    NoOpSymbolCatalog,
    make_client_order_id,
)
from execution.exchanges.okx import (
    FakeOKXClient,
    OKXLiveDispatcher,
    OKXSymbolCatalog,
    build_okx_dispatcher,
)
from execution.exchanges.symbol_catalog import CachedSymbolCatalog
from execution.exchanges.types import (
    ExchangeRequest,
    ExchangeResponse,
    ExchangeResponseStatus,
)
from execution.pending_orders import PendingOrder, PendingOrderStatus
from risk import (
    GuardContext,
    GuardResult,
    SymbolSupportedGuard,
)


# ================================================================== #
# Helpers
# ================================================================== #
def make_order(
    symbol="crypto:OKX:BTC/USDT:USDT",
    notional=500.0,
    mode="live",
    order_id=42,
    created_at=None,
) -> PendingOrder:
    return PendingOrder(
        strategy_id="s1",
        symbol=symbol,
        side="long",
        target_notional_usd=notional,
        mode=mode,
        id=order_id,
        created_at=created_at or datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc),
    )


# ================================================================== #
# make_client_order_id
# ================================================================== #
def test_coid_deterministic():
    """Same args → same id (idempotency contract)."""
    ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    a = make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts)
    b = make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts)
    assert a == b


def test_coid_changes_on_any_input_change():
    ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    base = make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts)
    # different strategy
    assert base != make_client_order_id(strategy_id="s2", symbol="BTC", side="long", intent_ts=ts)
    # different symbol
    assert base != make_client_order_id(strategy_id="s1", symbol="ETH", side="long", intent_ts=ts)
    # different side
    assert base != make_client_order_id(strategy_id="s1", symbol="BTC", side="short", intent_ts=ts)
    # different ts
    ts2 = datetime(2026, 4, 25, 12, 0, 1, tzinfo=timezone.utc)
    assert base != make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts2)
    # different mode
    assert base != make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts, mode="paper")


def test_coid_naive_ts_treated_as_utc():
    ts_naive = datetime(2026, 4, 25, 12, 0)
    ts_aware = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    assert (
        make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts_naive)
        == make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts_aware)
    )


def test_coid_satisfies_okx_format():
    """OKX clOrdId: 1-32 alphanumeric/underscore/dash."""
    import re
    ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    coid = make_client_order_id(strategy_id="s1", symbol="BTC", side="long", intent_ts=ts)
    assert re.match(r"^[A-Za-z0-9_-]{1,32}$", coid)
    assert len(coid) <= 32


def test_coid_rejects_empty_inputs():
    ts = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        make_client_order_id(strategy_id="", symbol="BTC", side="long", intent_ts=ts)


# ================================================================== #
# SymbolCatalog backends
# ================================================================== #
def test_noop_catalog_supports_everything():
    c = NoOpSymbolCatalog()
    assert c.supports("anything")
    assert c.supports("crypto:OKX:GHOST/USDT:USDT")


def test_inmemory_catalog_only_supports_seeded():
    c = InMemorySymbolCatalog({"crypto:OKX:BTC/USDT:USDT"})
    assert c.supports("crypto:OKX:BTC/USDT:USDT")
    assert not c.supports("crypto:OKX:GHOST/USDT:USDT")


def test_inmemory_catalog_add_grows_set():
    c = InMemorySymbolCatalog()
    c.add("X")
    c.add_many(["Y", "Z"])
    assert c.all_supported() == {"X", "Y", "Z"}


def test_cached_catalog_loads_on_first_access():
    calls = {"n": 0}
    def loader():
        calls["n"] += 1
        return {"BTC", "ETH"}
    c = CachedSymbolCatalog(loader, ttl_seconds=10)
    c.supports("BTC")
    c.supports("ETH")
    assert calls["n"] == 1   # cached after first


def test_cached_catalog_refresh_busts():
    calls = {"n": 0}
    def loader():
        calls["n"] += 1
        return {"BTC"}
    c = CachedSymbolCatalog(loader, ttl_seconds=600)
    c.supports("BTC")
    c.refresh()
    c.supports("BTC")
    assert calls["n"] == 2


def test_cached_catalog_first_load_failure_returns_empty():
    """Fail-closed when no fallback exists — caller can't tell what's listed."""
    def loader():
        raise ConnectionError("boom")
    c = CachedSymbolCatalog(loader, ttl_seconds=10)
    assert c.all_supported() == set()
    assert not c.supports("BTC")


def test_okx_symbol_catalog_uses_client():
    fake = FakeOKXClient(instruments={"crypto:OKX:BTC/USDT:USDT"})
    c = OKXSymbolCatalog(fake, ttl_seconds=10)
    assert c.supports("crypto:OKX:BTC/USDT:USDT")
    assert not c.supports("crypto:OKX:GHOST/USDT:USDT")
    assert fake.fetch_calls == 1


# ================================================================== #
# G2 SymbolSupportedGuard
# ================================================================== #
def test_g2_construction_requires_catalog():
    with pytest.raises(ValueError, match="catalog"):
        SymbolSupportedGuard()


def test_g2_allows_when_symbol_in_catalog():
    c = InMemorySymbolCatalog({"crypto:OKX:BTC/USDT:USDT"})
    g = SymbolSupportedGuard(catalog=c)
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g2_denies_unknown_symbol():
    c = InMemorySymbolCatalog({"crypto:OKX:BTC/USDT:USDT"})
    g = SymbolSupportedGuard(catalog=c)
    d = g.check(
        make_order(symbol="crypto:OKX:GHOST/USDT:USDT"),
        GuardContext(capital_usd=10_000),
    )
    assert d.result == GuardResult.DENY
    assert "GHOST" in d.reason


def test_g2_noop_catalog_allows_everything():
    g = SymbolSupportedGuard(catalog=NoOpSymbolCatalog())
    d = g.check(make_order(symbol="anything"), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW


def test_g2_catalog_failure_fails_open():
    class BadCatalog:
        def supports(self, s):
            raise ConnectionError("catalog down")
        def all_supported(self): return set()
    g = SymbolSupportedGuard(catalog=BadCatalog())
    d = g.check(make_order(), GuardContext(capital_usd=10_000))
    assert d.result == GuardResult.ALLOW
    assert "fail-open" in d.reason


# ================================================================== #
# FakeOKXClient
# ================================================================== #
def test_fake_client_default_response_is_accepted():
    fake = FakeOKXClient()
    req = ExchangeRequest(
        client_order_id="test-1", symbol="BTC", side="long", notional_usd=100,
    )
    resp = fake.place_order(req)
    assert resp.status == ExchangeResponseStatus.ACCEPTED
    assert resp.exchange_order_id == "FAKE-test-1"
    assert fake.place_calls == [req]


def test_fake_client_returns_duplicate_for_known_coid():
    fake = FakeOKXClient()
    req = ExchangeRequest(client_order_id="dup-1", symbol="BTC", side="long", notional_usd=100)
    fake.place_order(req)
    resp2 = fake.place_order(req)
    assert resp2.status == ExchangeResponseStatus.DUPLICATE


def test_fake_client_serves_programmed_responses_in_order():
    fake = FakeOKXClient(place_responses=[
        ExchangeResponse(status=ExchangeResponseStatus.FILLED, exchange_order_id="A",
                         filled_notional_usd=100),
        ExchangeResponse(status=ExchangeResponseStatus.REJECTED, error_code="51000",
                         error_message="bad"),
    ])
    r1 = fake.place_order(ExchangeRequest(client_order_id="x1", symbol="BTC", side="long", notional_usd=100))
    r2 = fake.place_order(ExchangeRequest(client_order_id="x2", symbol="BTC", side="long", notional_usd=100))
    assert r1.status == ExchangeResponseStatus.FILLED
    assert r2.status == ExchangeResponseStatus.REJECTED


def test_fake_client_cancel_recorded():
    fake = FakeOKXClient(cancel_result=True)
    assert fake.cancel_order("coid-1")
    assert fake.cancel_calls == ["coid-1"]


# ================================================================== #
# OKXLiveDispatcher — mode validation
# ================================================================== #
def test_dispatcher_construction_rejects_bad_mode():
    with pytest.raises(ValueError, match="mode must be"):
        OKXLiveDispatcher(FakeOKXClient(), mode="shadow")


def test_dispatcher_mode_property():
    d = OKXLiveDispatcher(FakeOKXClient(), mode="paper")
    assert d.mode == "paper"


# ================================================================== #
# OKXLiveDispatcher — status mapping
# ================================================================== #
def test_dispatcher_filled_response_maps_to_filled():
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.FILLED,
        exchange_order_id="X1",
        filled_notional_usd=500.0,
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.FILLED
    assert res.detail["exchange_order_id"] == "X1"
    assert res.detail["fill_notional"] == 500.0


def test_dispatcher_accepted_response_maps_to_submitted():
    """ACCEPTED → SUBMITTED (worker will poll for fill in F.1.x)."""
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.ACCEPTED,
        exchange_order_id="X2",
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.SUBMITTED


def test_dispatcher_partially_filled_maps_to_partially_filled():
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.PARTIALLY_FILLED,
        exchange_order_id="X3",
        filled_notional_usd=300.0,
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.PARTIALLY_FILLED


def test_dispatcher_rejected_maps_to_rejected_with_error():
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.REJECTED,
        error_code="51000", error_message="insufficient balance",
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.REJECTED
    assert "51000" in res.last_error
    assert "insufficient balance" in res.last_error


def test_dispatcher_duplicate_maps_to_submitted():
    """Idempotent retry: re-dispatch returns the prior order's state."""
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.DUPLICATE,
        exchange_order_id="X-prior",
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.SUBMITTED


def test_dispatcher_network_error_maps_to_rejected():
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.NETWORK_ERROR,
        error_message="connection reset",
    ))
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.REJECTED
    assert "okx_network" in res.last_error


def test_dispatcher_client_exception_maps_to_rejected():
    """Client raising → REJECTED with network_error tag."""
    class BoomClient:
        def place_order(self, req): raise ConnectionError("hard fail")
        def cancel_order(self, c): return False
        def fetch_instruments(self): return set()
    d = OKXLiveDispatcher(BoomClient())
    res = d.dispatch(make_order())
    assert res.terminal_status == PendingOrderStatus.REJECTED
    assert "network_error" in res.last_error


# ================================================================== #
# Idempotency at the dispatcher level
# ================================================================== #
def test_dispatcher_resubmit_uses_same_coid():
    """Re-dispatching the same order row → same client_order_id →
    exchange returns DUPLICATE → mapped to SUBMITTED. No double-fill."""
    fake = FakeOKXClient(place_response=ExchangeResponse(
        status=ExchangeResponseStatus.ACCEPTED,
        exchange_order_id="X-once",
    ))
    d = OKXLiveDispatcher(fake)
    order = make_order()

    res1 = d.dispatch(order)
    res2 = d.dispatch(order)

    assert res1.terminal_status == PendingOrderStatus.SUBMITTED
    # Second submission: FakeOKXClient sees same coid → DUPLICATE → SUBMITTED
    assert res2.terminal_status == PendingOrderStatus.SUBMITTED
    # Same coid sent both times
    assert fake.place_calls[0].client_order_id == fake.place_calls[1].client_order_id


def test_dispatcher_zero_notional_rejected_without_call():
    """Belt+braces: dispatcher should refuse to even ask the exchange
    about a zero-size order."""
    fake = FakeOKXClient()
    d = OKXLiveDispatcher(fake)
    res = d.dispatch(make_order(notional=0))
    assert res.terminal_status == PendingOrderStatus.REJECTED
    assert "build_request" in res.last_error
    assert fake.place_calls == []


# ================================================================== #
# build_okx_dispatcher factory
# ================================================================== #
def test_factory_returns_none_when_no_credentials():
    class S:
        okx_api_key = ""
        okx_api_secret = ""
        okx_api_passphrase = ""
    assert build_okx_dispatcher(S()) is None


def test_factory_raises_not_implemented_until_ccxt_wired():
    """Round 32 stub — full ccxt wiring lands in F.1.x."""
    class S:
        okx_api_key = "x"
        okx_api_secret = "y"
        okx_api_passphrase = "z"
    with pytest.raises(NotImplementedError, match="ccxt"):
        build_okx_dispatcher(S())
