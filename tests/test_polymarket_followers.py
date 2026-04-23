"""Tests for polymarket.followers — CopyWhale decision logic + PaperBook."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket.followers.base import AlertContext
from polymarket.followers.copy_whale import CopyWhaleFollower


NOW = datetime.now(timezone.utc)


def _ctx(
    tier: str = "A",
    notional: float = 500.0,
    price: float = 0.5,
    match_time: datetime | None = None,
) -> AlertContext:
    return AlertContext(
        wallet_address="0xabc",
        tx_hash="0xtx",
        event_index=0,
        tier=tier,
        condition_id="0xcond",
        market_question="Test market?",
        market_category="Politics",
        outcome="Yes",
        side="BUY",
        price=price,
        size=notional / price if price > 0 else 0,
        notional=notional,
        match_time=match_time or NOW,
    )


class TestCopyWhaleFollower:
    @pytest.fixture
    def follower(self):
        return CopyWhaleFollower()

    def test_follows_tier_a(self, follower):
        d = follower.on_alert(_ctx(tier="A"))
        assert d.is_follow()
        assert d.proposed_stake_pct == 0.03

    def test_follows_tier_b(self, follower):
        d = follower.on_alert(_ctx(tier="B"))
        assert d.is_follow()
        assert d.proposed_stake_pct == 0.02

    def test_follows_tier_c(self, follower):
        d = follower.on_alert(_ctx(tier="C"))
        assert d.is_follow()
        assert d.proposed_stake_pct == 0.01

    def test_follows_emerging(self, follower):
        d = follower.on_alert(_ctx(tier="emerging"))
        assert d.is_follow()
        assert d.proposed_stake_pct == 0.005

    def test_skips_volatile(self, follower):
        d = follower.on_alert(_ctx(tier="volatile"))
        assert d.decision == "skip"
        assert "tier_not_tracked" in d.reason

    def test_skips_excluded(self, follower):
        d = follower.on_alert(_ctx(tier="excluded"))
        assert d.decision == "skip"

    def test_skips_small_notional(self, follower):
        d = follower.on_alert(_ctx(tier="A", notional=50))
        assert d.decision == "skip"
        assert "notional_too_small" in d.reason

    def test_skips_extreme_price_low(self, follower):
        d = follower.on_alert(_ctx(tier="A", price=0.02))
        assert d.decision == "skip"
        assert "price_extreme" in d.reason

    def test_skips_extreme_price_high(self, follower):
        d = follower.on_alert(_ctx(tier="A", price=0.99))
        assert d.decision == "skip"
        assert "price_extreme" in d.reason

    def test_skips_stale_trade(self, follower):
        d = follower.on_alert(_ctx(tier="A", match_time=NOW - timedelta(hours=3)))
        assert d.decision == "skip"
        assert "trade_too_old" in d.reason

    def test_registry_has_copy_whale(self):
        from polymarket.followers import REGISTRY, get

        assert "copy_whale" in REGISTRY
        assert get("copy_whale") is not None


# === PaperBook tests ===

from polymarket.storage.repo import SqliteRepo
from polymarket.followers.paper_book import PaperBook, PaperTradeEntry


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepo(db_path=tmp_path / "t.db")
    yield r
    r.close()


@pytest.fixture
def book(repo):
    return PaperBook(repo)


def _entry(
    cond: str = "0xcond",
    wallet: str = "0xwallet",
    price: float = 0.5,
    notional: float = 30.0,
    side: str = "BUY",
) -> PaperTradeEntry:
    return PaperTradeEntry(
        follower_name="copy_whale",
        source_wallet=wallet,
        source_tier="A",
        condition_id=cond,
        token_id="tok1",
        market_question="Q?",
        market_category="Politics",
        outcome="Yes",
        side=side,
        entry_price=price,
        entry_size=notional / price,
        entry_notional=notional,
        entry_time=NOW,
    )


class TestPaperBook:
    def test_enter_and_summary(self, book):
        book.enter_paper_trade(_entry())
        s = book.summary()
        assert s["total"] == 1
        assert s["open"] == 1
        assert s["closed"] == 0

    def test_has_open_position(self, book):
        book.enter_paper_trade(_entry())
        assert book.has_open_position("copy_whale", "0xwallet", "0xcond")
        assert not book.has_open_position("copy_whale", "0xwallet", "0xother")

    def test_resolve_win(self, book, repo):
        # Seed a resolved market (closed + YES token winner)
        conn = repo._connect()
        conn.execute(
            "INSERT INTO markets (condition_id, question, active, closed, fetched_at, updated_at) "
            "VALUES ('0xcond', 'Q?', 0, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO tokens (token_id, condition_id, outcome, price, winner, fetched_at) "
            "VALUES ('tok1', '0xcond', 'Yes', 1.0, 1, ?)",
            (NOW.isoformat(),),
        )
        conn.commit()

        book.enter_paper_trade(_entry(price=0.4, notional=40))  # 100 tokens
        result = book.scan_and_resolve()
        assert result["resolved"] == 1

        s = book.summary()
        assert s["closed"] == 1
        assert s["wins"] == 1
        # PnL = (1.0 - 0.4) × 100 = $60
        assert s["realized_pnl_usdc"] == pytest.approx(60.0)

    def test_resolve_loss(self, book, repo):
        conn = repo._connect()
        conn.execute(
            "INSERT INTO markets (condition_id, question, active, closed, fetched_at, updated_at) "
            "VALUES ('0xcond', 'Q?', 0, 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO tokens (token_id, condition_id, outcome, price, winner, fetched_at) "
            "VALUES ('tok1', '0xcond', 'Yes', 0.0, 0, ?)",
            (NOW.isoformat(),),
        )
        conn.commit()

        book.enter_paper_trade(_entry(price=0.4, notional=40))  # 100 tokens
        book.scan_and_resolve()

        s = book.summary()
        assert s["losses"] == 1
        # Lost entire 40 entry value; PnL = -40 (0 - 0.4) × 100
        assert s["realized_pnl_usdc"] == pytest.approx(-40.0)

    def test_resolve_still_open_when_market_not_closed(self, book, repo):
        conn = repo._connect()
        conn.execute(
            "INSERT INTO markets (condition_id, question, active, closed, fetched_at, updated_at) "
            "VALUES ('0xcond', 'Q?', 1, 0, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.commit()

        book.enter_paper_trade(_entry())
        result = book.scan_and_resolve()
        assert result["still_open"] == 1
        assert result["resolved"] == 0

    def test_record_decision_persists(self, book, repo):
        from polymarket.followers.base import FollowerDecision

        d = FollowerDecision(
            follower_name="copy_whale",
            follower_version="1.0",
            decision="follow",
            reason="tier_A_ok",
            decided_at=NOW,
            proposed_stake_pct=0.03,
            proposed_size_usdc=30.0,
        )
        src = _ctx(tier="A")
        rid = book.record_decision(d, src, paper_trade_id=None)
        assert rid > 0

        conn = repo._connect()
        rows = list(conn.execute("SELECT * FROM follower_decisions"))
        assert len(rows) == 1
        assert rows[0]["decision"] == "follow"
