"""Tests for whale-related repo methods (whale_stats, whale_tier_history, whale_trade_alerts)."""

from __future__ import annotations

import pytest

from polymarket.storage.repo import SqliteRepo


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepo(db_path=tmp_path / "t.db")
    yield r
    r.close()


class TestWhaleStats:
    def _stats(self, wallet="0xabc", tier="A"):
        return {
            "wallet_address": wallet,
            "tier": tier,
            "trade_count_90d": 25,
            "win_rate": 0.65,
            "cumulative_pnl": 12000,
            "avg_trade_size": 600,
            "segment_win_rates": [0.6, 0.65, 0.7],
            "stability_pass": True,
            "resolved_count": 20,
            "last_trade_at": "2026-04-20T12:00:00+00:00",
        }

    def test_upsert_new_wallet(self, repo):
        prev = repo.upsert_whale_stats(self._stats())
        assert prev is None
        assert repo.count_whales() == 1
        assert repo.count_whales(tier="A") == 1

    def test_upsert_records_initial_history(self, repo):
        repo.upsert_whale_stats(self._stats())
        conn = repo._connect()
        rows = list(conn.execute("SELECT * FROM whale_tier_history"))
        assert len(rows) == 1
        assert rows[0]["from_tier"] is None
        assert rows[0]["to_tier"] == "A"
        assert rows[0]["reason"] == "initial"

    def test_tier_change_records_history(self, repo):
        repo.upsert_whale_stats(self._stats(tier="C"))
        prev = repo.upsert_whale_stats(self._stats(tier="A"))
        assert prev == "C"
        conn = repo._connect()
        rows = list(conn.execute("SELECT * FROM whale_tier_history ORDER BY id"))
        assert len(rows) == 2
        assert rows[1]["from_tier"] == "C"
        assert rows[1]["to_tier"] == "A"
        assert rows[1]["reason"] == "promoted"

    def test_tier_unchanged_no_new_history(self, repo):
        repo.upsert_whale_stats(self._stats())
        repo.upsert_whale_stats({**self._stats(), "cumulative_pnl": 13000})
        conn = repo._connect()
        count = conn.execute("SELECT COUNT(*) AS c FROM whale_tier_history").fetchone()["c"]
        assert count == 1

    def test_list_by_tier(self, repo):
        repo.upsert_whale_stats(self._stats(wallet="0x1", tier="A"))
        repo.upsert_whale_stats(self._stats(wallet="0x2", tier="B"))
        repo.upsert_whale_stats(self._stats(wallet="0x3", tier="C"))
        assert len(repo.list_whales_by_tier("A", "B")) == 2
        assert len(repo.list_whales_by_tier("A", "B", "C")) == 3


class TestWhaleAlerts:
    def _alert(self, tx_hash="0xtx1", event_index=0, tier="A"):
        return {
            "wallet_address": "0xabc",
            "tx_hash": tx_hash,
            "event_index": event_index,
            "tier": tier,
            "condition_id": "0xcond",
            "market_question": "Q?",
            "side": "BUY",
            "outcome": "Yes",
            "size": 1000,
            "price": 0.5,
            "notional": 500,
            "match_time": "2026-04-20T12:00:00+00:00",
            "telegram_sent": True,
        }

    def test_record_new_alert(self, repo):
        assert repo.record_alert(self._alert()) is True
        assert repo.count_alerts() == 1

    def test_duplicate_alert_rejected(self, repo):
        repo.record_alert(self._alert())
        assert repo.record_alert(self._alert()) is False
        assert repo.count_alerts() == 1

    def test_different_event_index_allowed(self, repo):
        repo.record_alert(self._alert(event_index=0))
        assert repo.record_alert(self._alert(event_index=1)) is True
        assert repo.count_alerts() == 2

    def test_count_alerts_by_wallet(self, repo):
        repo.record_alert(self._alert())
        assert repo.count_alerts("0xabc") == 1
        assert repo.count_alerts("0xother") == 0
