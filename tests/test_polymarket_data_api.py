"""Tests for polymarket.clients.data_api — Data API client."""

from __future__ import annotations

from decimal import Decimal

import httpx

from polymarket.clients.data_api import DataApiClient, _normalize_trade_fields
from polymarket.models import Trade


def _mock(handlers):
    def dispatcher(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        for p, h in handlers.items():
            if path == p or path.startswith(p + "/"):
                return h(req)
        return httpx.Response(404, json={"error": "nf"})

    return httpx.MockTransport(dispatcher)


def _make_client(transport):
    return DataApiClient(client=httpx.Client(transport=transport))


class TestNormalizeTradeFields:
    def test_builds_id_from_tx_hash_and_event_index(self):
        out = _normalize_trade_fields(
            {"transactionHash": "0xabc", "eventIndex": 3, "conditionId": "0xc", "timestamp": 1700000000}
        )
        assert out["id"] == "0xabc:3"
        assert out["market"] == "0xc"
        assert out["match_time"] == 1700000000

    def test_uses_tokenId_as_asset_id(self):
        out = _normalize_trade_fields(
            {"transactionHash": "0xabc", "eventIndex": 0, "conditionId": "0xc", "tokenId": "tok1", "timestamp": 1}
        )
        assert out["asset_id"] == "tok1"

    def test_uppercase_side(self):
        out = _normalize_trade_fields(
            {"transactionHash": "0xabc", "eventIndex": 0, "conditionId": "0xc", "side": "buy", "timestamp": 1}
        )
        assert out["side"] == "BUY"

    def test_maker_taker_addresses_aliased(self):
        out = _normalize_trade_fields(
            {
                "transactionHash": "0xabc",
                "eventIndex": 0,
                "conditionId": "0xc",
                "maker": "0xmaker",
                "taker": "0xtaker",
                "timestamp": 1,
            }
        )
        assert out["maker_address"] == "0xmaker"
        assert out["taker_address"] == "0xtaker"


class TestGetMarketTrades:
    def test_parses_data_api_trade_format(self):
        def handler(_req):
            return httpx.Response(
                200,
                json=[
                    {
                        "transactionHash": "0xtx1",
                        "eventIndex": 0,
                        "conditionId": "0xc",
                        "tokenId": "tok1",
                        "price": "0.55",
                        "size": "100",
                        "side": "buy",
                        "timestamp": 1700000000,
                        "maker": "0xmaker",
                        "taker": "0xtaker",
                    }
                ],
            )

        client = _make_client(_mock({"/trades": handler}))
        trades = client.get_market_trades(market="0xc")
        assert len(trades) == 1
        assert isinstance(trades[0], Trade)
        assert trades[0].id == "0xtx1:0"
        assert trades[0].side == "BUY"
        assert trades[0].notional_usdc() == Decimal("55.00")
        assert trades[0].maker_address == "0xmaker"

    def test_skips_malformed(self):
        def handler(_req):
            return httpx.Response(
                200,
                json=[
                    {"transactionHash": "0xok", "eventIndex": 0, "conditionId": "0xc", "price": "0.5", "size": "1", "side": "BUY", "timestamp": 1700000000},
                    {"transactionHash": "0xbad", "eventIndex": 0, "conditionId": "0xc", "price": "0.5"},  # missing size/side
                ],
            )

        client = _make_client(_mock({"/trades": handler}))
        trades = client.get_market_trades(market="0xc")
        assert len(trades) == 1
        assert trades[0].id == "0xok:0"
