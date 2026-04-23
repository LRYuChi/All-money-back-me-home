"""Tests for E.1 — telegram_sent retry mechanism.

Targets: repo.mark_alert_sent / repo.get_unsent_alerts (SqliteRepo).
Integration (retry flow in pipeline.py) is not unit-tested here since it
would require heavy mocking of Telegram; covered by smoke test in
test_polymarket_pipeline later.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from polymarket.storage.repo import SqliteRepo


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepo(db_path=tmp_path / "test.db")
    yield r
    r.close()


def _insert_raw_alert(
    repo: SqliteRepo,
    *,
    wallet: str,
    tx_hash: str,
    event_index: int = 0,
    tier: str = "A",
    telegram_sent: int = 0,
    alerted_at_offset_hours: float = 0,
    condition_id: str = "0xMKT",
    match_time_offset_hours: float = 0,
) -> None:
    """Insert a raw alert row with custom alerted_at timing (for retry window tests)."""
    conn = repo._connect()
    alerted_at = (
        datetime.now(timezone.utc) - timedelta(hours=alerted_at_offset_hours)
    ).isoformat()
    match_time = (
        datetime.now(timezone.utc) - timedelta(hours=match_time_offset_hours)
    ).isoformat()
    conn.execute(
        """INSERT INTO whale_trade_alerts (wallet_address, tx_hash, event_index, tier,
           condition_id, market_question, side, outcome, size, price, notional,
           match_time, alerted_at, telegram_sent)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            wallet, tx_hash, event_index, tier, condition_id,
            "Will X happen?", "BUY", "Yes", 1000, 0.5, 500,
            match_time, alerted_at, telegram_sent,
        ),
    )
    conn.commit()


class TestMarkAlertSent:
    def test_marks_successfully(self, repo):
        _insert_raw_alert(repo, wallet="0xABC", tx_hash="tx1", telegram_sent=0)
        assert repo.mark_alert_sent("0xABC", "tx1", 0) is True

        conn = repo._connect()
        row = conn.execute(
            "SELECT telegram_sent FROM whale_trade_alerts WHERE wallet_address=? AND tx_hash=?",
            ("0xABC", "tx1"),
        ).fetchone()
        assert row["telegram_sent"] == 1

    def test_returns_false_when_not_found(self, repo):
        assert repo.mark_alert_sent("0xNONE", "txNone", 0) is False

    def test_idempotent_on_already_sent(self, repo):
        _insert_raw_alert(repo, wallet="0xABC", tx_hash="tx1", telegram_sent=1)
        # Already 1, update is no-op but still returns True (row matched)
        assert repo.mark_alert_sent("0xABC", "tx1", 0) is True

    def test_preserves_composite_key_uniqueness(self, repo):
        # 同 wallet + tx_hash 但不同 event_index 應獨立
        _insert_raw_alert(repo, wallet="0xABC", tx_hash="tx1", event_index=0, telegram_sent=0)
        _insert_raw_alert(repo, wallet="0xABC", tx_hash="tx1", event_index=1, telegram_sent=0)
        assert repo.mark_alert_sent("0xABC", "tx1", 0) is True

        conn = repo._connect()
        rows = conn.execute(
            "SELECT event_index, telegram_sent FROM whale_trade_alerts "
            "WHERE wallet_address=? AND tx_hash=? ORDER BY event_index",
            ("0xABC", "tx1"),
        ).fetchall()
        assert [dict(r) for r in rows] == [
            {"event_index": 0, "telegram_sent": 1},
            {"event_index": 1, "telegram_sent": 0},
        ]


class TestGetUnsentAlerts:
    def test_returns_empty_when_all_sent(self, repo):
        _insert_raw_alert(repo, wallet="0xA", tx_hash="t1", telegram_sent=1)
        assert repo.get_unsent_alerts() == []

    def test_returns_unsent_only(self, repo):
        _insert_raw_alert(repo, wallet="0xA", tx_hash="t1", telegram_sent=1)
        _insert_raw_alert(repo, wallet="0xB", tx_hash="t2", telegram_sent=0)
        _insert_raw_alert(repo, wallet="0xC", tx_hash="t3", telegram_sent=0)
        unsent = repo.get_unsent_alerts()
        assert len(unsent) == 2
        assert {u["wallet_address"] for u in unsent} == {"0xB", "0xC"}

    def test_filters_by_hours_window(self, repo):
        # 25 小時前的不該回
        _insert_raw_alert(
            repo, wallet="0xOLD", tx_hash="told", telegram_sent=0,
            alerted_at_offset_hours=25,
        )
        _insert_raw_alert(
            repo, wallet="0xNEW", tx_hash="tnew", telegram_sent=0,
            alerted_at_offset_hours=0.5,
        )
        unsent = repo.get_unsent_alerts(hours=24)
        assert len(unsent) == 1
        assert unsent[0]["wallet_address"] == "0xNEW"

    def test_includes_market_category_via_join(self, repo):
        # 先插 market
        conn = repo._connect()
        conn.execute(
            """INSERT INTO markets (condition_id, question, category, active, closed,
               fetched_at, updated_at) VALUES (?, ?, ?, 1, 0, ?, ?)""",
            ("0xMKT", "Test?", "Politics", "2026-04-23T00:00:00+00:00", "2026-04-23T00:00:00+00:00"),
        )
        conn.commit()
        _insert_raw_alert(repo, wallet="0xA", tx_hash="t1", telegram_sent=0, condition_id="0xMKT")

        unsent = repo.get_unsent_alerts()
        assert len(unsent) == 1
        assert unsent[0]["market_category"] == "Politics"

    def test_orders_by_oldest_first(self, repo):
        _insert_raw_alert(
            repo, wallet="0xNEW", tx_hash="tnew", telegram_sent=0,
            alerted_at_offset_hours=0.5,
        )
        _insert_raw_alert(
            repo, wallet="0xOLD", tx_hash="told", telegram_sent=0,
            alerted_at_offset_hours=2.0,
        )
        unsent = repo.get_unsent_alerts()
        # Oldest first
        assert [u["wallet_address"] for u in unsent] == ["0xOLD", "0xNEW"]

    def test_respects_limit(self, repo):
        for i in range(10):
            _insert_raw_alert(
                repo, wallet=f"0x{i}", tx_hash=f"t{i}", telegram_sent=0,
                alerted_at_offset_hours=i * 0.1,
            )
        unsent = repo.get_unsent_alerts(limit=5)
        assert len(unsent) == 5


class TestRetryIntegration:
    """Simulates the pipeline retry flow at repo level."""

    def test_round_trip_send_fail_retry_success(self, repo):
        # 1. Alert inserted unsent
        _insert_raw_alert(repo, wallet="0xWHALE", tx_hash="tx1", telegram_sent=0,
                          alerted_at_offset_hours=0.5)
        # 2. Retrieve via get_unsent_alerts
        unsent = repo.get_unsent_alerts()
        assert len(unsent) == 1
        target = unsent[0]

        # 3. Simulated retry success → mark_alert_sent
        ok = repo.mark_alert_sent(
            target["wallet_address"], target["tx_hash"], int(target["event_index"])
        )
        assert ok is True

        # 4. Next get_unsent_alerts returns empty
        assert repo.get_unsent_alerts() == []
