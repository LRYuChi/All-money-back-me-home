"""Tests for polymarket.storage.repo — SQLite repository."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from polymarket.models import Level, Market, OrderBook, Token, Trade
from polymarket.storage.repo import SqliteRepo


@pytest.fixture
def repo(tmp_path):
    db_path = tmp_path / "test.db"
    r = SqliteRepo(db_path=db_path)
    yield r
    r.close()


class TestMarkets:
    def test_upsert_and_count(self, repo):
        m = Market(
            condition_id="0xA",
            question="Q?",
            tokens=[
                Token(token_id="t1", outcome="Yes"),
                Token(token_id="t2", outcome="No"),
            ],
        )
        repo.upsert_market(m)
        assert repo.count_markets() == 1

    def test_upsert_is_idempotent(self, repo):
        m = Market(condition_id="0xA", question="Q1?")
        repo.upsert_market(m)
        repo.upsert_market(m)
        assert repo.count_markets() == 1

    def test_upsert_updates_existing(self, repo):
        repo.upsert_market(Market(condition_id="0xA", question="Q1?"))
        repo.upsert_market(Market(condition_id="0xA", question="Q1-updated?"))
        assert repo.count_markets() == 1


class TestBookSnapshots:
    def test_insert_and_count(self, repo):
        # 必須先有 market 因為 tokens 有 FK，但 order_book_snapshots 沒有
        book = OrderBook(
            market="0xA",
            asset_id="tok1",
            bids=[Level(price="0.50", size="10")],
            asks=[Level(price="0.52", size="5")],
        )
        repo.insert_book_snapshot(book)
        assert repo.count_book_snapshots() == 1
        assert repo.count_book_snapshots(token_id="tok1") == 1
        assert repo.count_book_snapshots(token_id="other") == 0

    def test_multiple_snapshots_same_token(self, repo):
        book = OrderBook(
            market="0xA",
            asset_id="tok1",
            bids=[Level(price="0.50", size="10")],
            asks=[Level(price="0.52", size="5")],
        )
        repo.insert_book_snapshot(book)
        repo.insert_book_snapshot(book)
        assert repo.count_book_snapshots(token_id="tok1") == 2


class TestTrades:
    def _trade(self, id_: str = "t1") -> Trade:
        return Trade(
            id=id_,
            market="0xA",
            asset_id="tok1",
            price="0.55",
            size=Decimal("100"),
            side="BUY",
            match_time=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        )

    def test_insert_new_returns_true(self, repo):
        assert repo.insert_trade(self._trade("t1")) is True
        assert repo.count_trades() == 1

    def test_insert_duplicate_returns_false(self, repo):
        repo.insert_trade(self._trade("t1"))
        assert repo.insert_trade(self._trade("t1")) is False
        assert repo.count_trades() == 1

    def test_batch_insert_reports_new_and_dup(self, repo):
        repo.insert_trade(self._trade("t1"))
        trades = [self._trade("t1"), self._trade("t2"), self._trade("t3")]
        new_count, dup_count = repo.insert_trades(trades)
        assert new_count == 2
        assert dup_count == 1
        assert repo.count_trades() == 3

    def test_count_trades_by_market(self, repo):
        repo.insert_trade(self._trade("t1"))
        t_other = Trade(
            id="t99",
            market="0xB",
            asset_id="tok99",
            price="0.1",
            size="1",
            side="SELL",
            match_time=datetime(2026, 4, 20, tzinfo=timezone.utc),
        )
        repo.insert_trade(t_other)
        assert repo.count_trades("0xA") == 1
        assert repo.count_trades("0xB") == 1
        assert repo.count_trades() == 2
