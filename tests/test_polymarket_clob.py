"""Tests for polymarket.clients.clob — CLOB REST client (mock-based)."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from polymarket.clients.clob import ClobClient
from polymarket.models import Market, OrderBook


def _mock_transport(route_handlers: dict[str, callable]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        for prefix, h in route_handlers.items():
            if path == prefix or path.startswith(prefix + "/"):
                return h(request)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> ClobClient:
    return ClobClient(client=httpx.Client(transport=transport))


class TestGetMarkets:
    def test_parses_markets_and_cursor(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "condition_id": "0x001",
                            "question": "Will BTC hit 100k?",
                            "tokens": [
                                {"token_id": "t1", "outcome": "Yes", "price": 0.7},
                                {"token_id": "t2", "outcome": "No", "price": 0.3},
                            ],
                        }
                    ],
                    "next_cursor": "page2",
                },
            )

        client = _make_client(_mock_transport({"/markets": handler}))
        markets, cursor = client.get_markets()

        assert len(markets) == 1
        assert isinstance(markets[0], Market)
        assert markets[0].condition_id == "0x001"
        assert markets[0].yes_token().price == 0.7
        assert cursor == "page2"

    def test_iter_markets_stops_on_empty_cursor(self):
        calls = [0]

        def handler(_req: httpx.Request) -> httpx.Response:
            calls[0] += 1
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"condition_id": f"0x{calls[0]:03d}", "question": f"Q{calls[0]}", "tokens": []}
                    ],
                    "next_cursor": "" if calls[0] >= 2 else f"page{calls[0]+1}",
                },
            )

        client = _make_client(_mock_transport({"/markets": handler}))
        markets = client.iter_markets(max_pages=10)
        assert len(markets) == 2
        assert calls[0] == 2

    def test_iter_markets_stops_on_lte_sentinel(self):
        calls = [0]

        def handler(_req: httpx.Request) -> httpx.Response:
            calls[0] += 1
            return httpx.Response(
                200,
                json={
                    "data": [{"condition_id": "0x001", "question": "Q", "tokens": []}],
                    "next_cursor": "LTE=",
                },
            )

        client = _make_client(_mock_transport({"/markets": handler}))
        markets = client.iter_markets(max_pages=10)
        assert len(markets) == 1
        assert calls[0] == 1


class TestGetBook:
    def test_parses_book(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "market": "0x001",
                    "asset_id": "tok1",
                    "hash": "h",
                    "bids": [{"price": "0.50", "size": "100"}, {"price": "0.49", "size": "200"}],
                    "asks": [{"price": "0.52", "size": "50"}, {"price": "0.53", "size": "150"}],
                },
            )

        client = _make_client(_mock_transport({"/book": handler}))
        book = client.get_book("tok1")
        assert isinstance(book, OrderBook)
        assert book.best_bid().price == Decimal("0.50")
        assert book.best_ask().price == Decimal("0.52")
        assert book.mid_price() == Decimal("0.51")


class TestRetryAndErrors:
    def test_rate_limit_then_success(self):
        calls = [0]

        def handler(_req: httpx.Request) -> httpx.Response:
            calls[0] += 1
            if calls[0] == 1:
                return httpx.Response(429, json={"error": "rate limited"})
            return httpx.Response(
                200, json={"data": [], "next_cursor": ""}
            )

        client = _make_client(_mock_transport({"/markets": handler}))
        markets, _ = client.get_markets()
        assert calls[0] == 2
        assert markets == []

    def test_raises_after_retries_exhausted(self):
        def handler(_req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        client = _make_client(_mock_transport({"/markets": handler}))
        with pytest.raises(RuntimeError, match="failed after"):
            client.get_markets()
