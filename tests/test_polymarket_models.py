"""Tests for polymarket.models — Pydantic v2 validation."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from polymarket.models import Level, Market, OrderBook, Token, Trade


class TestToken:
    def test_minimal(self):
        t = Token(token_id="abc", outcome="Yes")
        assert t.price is None
        assert t.winner is None
        assert t.is_binary is True

    def test_multi_outcome_allowed(self):
        """多選項市場的 outcome 可以是任意字串（例如候選人名、州名）."""
        t = Token(token_id="abc", outcome="Arizona State")
        assert t.outcome == "Arizona State"
        assert t.is_binary is False


class TestMarket:
    def test_parse_end_date_iso_z(self):
        m = Market(
            condition_id="0xabc",
            question="Will X happen?",
            end_date_iso="2026-12-31T23:59:59Z",
        )
        assert m.end_date_iso == datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    def test_parse_end_date_none(self):
        m = Market(condition_id="0xabc", question="Q?", end_date_iso=None)
        assert m.end_date_iso is None

    def test_parse_end_date_empty_string(self):
        m = Market(condition_id="0xabc", question="Q?", end_date_iso="")
        assert m.end_date_iso is None

    def test_yes_no_tokens(self):
        m = Market(
            condition_id="0xabc",
            question="Q?",
            tokens=[
                Token(token_id="1", outcome="Yes", price=0.6),
                Token(token_id="2", outcome="No", price=0.4),
            ],
        )
        assert m.yes_token().token_id == "1"
        assert m.no_token().token_id == "2"
        assert m.is_binary() is True

    def test_multi_outcome_market(self):
        m = Market(
            condition_id="0xabc",
            question="Winner?",
            tokens=[
                Token(token_id="1", outcome="Alice", price=0.4),
                Token(token_id="2", outcome="Bob", price=0.35),
                Token(token_id="3", outcome="Carol", price=0.25),
            ],
        )
        assert m.is_binary() is False
        assert m.yes_token() is None

    def test_ignore_extra_fields(self):
        m = Market.model_validate(
            {
                "condition_id": "0xabc",
                "question": "Q?",
                "unknown_field": "whatever",
                "nested": {"also": "ignored"},
            }
        )
        assert m.condition_id == "0xabc"


class TestOrderBook:
    def _book(self):
        return OrderBook(
            market="0xabc",
            asset_id="tok1",
            bids=[Level(price="0.50", size="10"), Level(price="0.49", size="20")],
            asks=[Level(price="0.52", size="5"), Level(price="0.53", size="15")],
        )

    def test_best_bid_is_max_price(self):
        b = self._book()
        assert b.best_bid().price == Decimal("0.50")

    def test_best_ask_is_min_price(self):
        b = self._book()
        assert b.best_ask().price == Decimal("0.52")

    def test_mid_and_spread(self):
        b = self._book()
        assert b.mid_price() == Decimal("0.51")
        assert b.spread() == Decimal("0.02")

    def test_empty_book_returns_none(self):
        b = OrderBook(market="0xabc", asset_id="tok1")
        assert b.best_bid() is None
        assert b.best_ask() is None
        assert b.mid_price() is None
        assert b.spread() is None


class TestTrade:
    def test_parse_iso_match_time(self):
        t = Trade(
            id="t1",
            market="0xabc",
            asset_id="tok1",
            price="0.55",
            size="100",
            side="BUY",
            match_time="2026-04-20T12:00:00Z",
        )
        assert t.match_time == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)
        assert t.notional_usdc() == Decimal("55.00")

    def test_parse_unix_timestamp_int(self):
        t = Trade(
            id="t1",
            market="0xabc",
            asset_id="tok1",
            price="0.5",
            size="1",
            side="SELL",
            match_time=1700000000,
        )
        assert t.match_time.tzinfo is timezone.utc

    def test_parse_unix_timestamp_str(self):
        t = Trade(
            id="t1",
            market="0xabc",
            asset_id="tok1",
            price="0.5",
            size="1",
            side="SELL",
            match_time="1700000000",
        )
        assert t.match_time.tzinfo is timezone.utc

    def test_invalid_side(self):
        with pytest.raises(ValidationError):
            Trade(
                id="t1",
                market="0xabc",
                asset_id="tok1",
                price="0.5",
                size="1",
                side="HOLD",
                match_time="2026-04-20T12:00:00Z",
            )
