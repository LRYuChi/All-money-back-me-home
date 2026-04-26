"""Tests for smart_money router — status + leaderboard endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from src.routers import smart_money as sm_router
    sm_router._cache.clear()
    app = FastAPI()
    app.include_router(sm_router.router)
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────
# Status endpoint
# ─────────────────────────────────────────────────────────────────────

class TestStatus:
    def test_returns_unavailable_when_supabase_none(self, client):
        with patch("src.routers.smart_money.get_supabase", return_value=None):
            resp = client.get("/api/smart-money/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False
        assert "未設定" in data["reason"]

    def test_returns_configured_with_counts(self, client):
        sb = MagicMock()
        latest_mock = MagicMock()
        latest_mock.data = [{"snapshot_date": "2026-04-22"}]
        rc_mock = MagicMock()
        rc_mock.count = 50
        wc_mock = MagicMock()
        wc_mock.count = 200

        # Chain 1: latest snapshot
        sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = latest_mock
        # Because all calls return same chain, we need more nuanced mocks
        # Use side_effect on table() to return different chains for each call

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                # rankings order by date DESC
                m.select.return_value.order.return_value.limit.return_value.execute.return_value = latest_mock
            elif call_count["n"] == 2:
                # rankings count for target date
                m.select.return_value.eq.return_value.execute.return_value = rc_mock
            elif call_count["n"] == 3:
                # wallet count
                m.select.return_value.execute.return_value = wc_mock
            return m

        sb.table.side_effect = table_side_effect

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["latest_snapshot_date"] == "2026-04-22"
        assert data["ranking_count"] == 50
        assert data["wallet_count"] == 200


# ─────────────────────────────────────────────────────────────────────
# Leaderboard endpoint
# ─────────────────────────────────────────────────────────────────────

class TestLeaderboard:
    def test_returns_unavailable_when_supabase_none(self, client):
        with patch("src.routers.smart_money.get_supabase", return_value=None):
            resp = client.get("/api/smart-money/leaderboard?limit=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is False

    def test_returns_empty_when_no_snapshots(self, client):
        sb = MagicMock()
        latest = MagicMock()
        latest.data = []
        sb.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = latest

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/leaderboard?limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_date"] is None
        assert data["count"] == 0
        assert data["rankings"] == []

    def test_returns_ranked_list(self, client):
        sb = MagicMock()

        # First call: latest snapshot date
        latest = MagicMock()
        latest.data = [{"snapshot_date": "2026-04-22"}]

        # Second call: rankings JOIN wallets
        rankings_mock = MagicMock()
        rankings_mock.data = [
            {
                "rank": 1,
                "score": 0.92,
                "metrics": {"sortino": 2.5, "pf": 1.8, "mdd": 0.12},
                "ai_analysis": None,
                "sm_wallets": {
                    "address": "0xABC123",
                    "tags": ["whitelisted"],
                    "last_active_at": "2026-04-22T10:00:00+00:00",
                    "notes": None,
                },
            },
            {
                "rank": 2,
                "score": 0.87,
                "metrics": {"sortino": 2.1, "pf": 1.5, "mdd": 0.15},
                "ai_analysis": None,
                "sm_wallets": {
                    "address": "0xDEF456",
                    "tags": [],
                    "last_active_at": "2026-04-21T15:00:00+00:00",
                    "notes": "watchlist",
                },
            },
        ]

        call_count = {"n": 0}

        def table_side_effect(name):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                m.select.return_value.order.return_value.limit.return_value.execute.return_value = latest
            else:
                m.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = rankings_mock
            return m

        sb.table.side_effect = table_side_effect

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/leaderboard?limit=10")

        assert resp.status_code == 200
        data = resp.json()
        assert data["snapshot_date"] == "2026-04-22"
        assert data["count"] == 2
        r0 = data["rankings"][0]
        assert r0["rank"] == 1
        assert r0["address"] == "0xABC123"
        assert r0["score"] == pytest.approx(0.92)
        assert r0["metrics"]["sortino"] == 2.5
        assert "whitelisted" in r0["tags"]

    def test_explicit_snapshot_date(self, client):
        sb = MagicMock()
        rankings_mock = MagicMock()
        rankings_mock.data = []
        sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = rankings_mock

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/leaderboard?snapshot_date=2026-04-01&limit=5")

        assert resp.status_code == 200
        data = resp.json()
        # 沒 fallback 去查 latest，因為明確指定了日期
        assert data["snapshot_date"] == "2026-04-01"


# ─────────────────────────────────────────────────────────────────────
# R92 — Skip breakdown endpoint
# ─────────────────────────────────────────────────────────────────────

class TestSkipBreakdown:
    def test_returns_unavailable_when_supabase_none(self, client):
        with patch("src.routers.smart_money.get_supabase", return_value=None):
            resp = client.get("/api/smart-money/skip-breakdown")
        assert resp.status_code == 200
        assert resp.json()["configured"] is False

    def test_aggregates_by_symbol_and_wallet(self, client):
        sb = MagicMock()
        rows_mock = MagicMock()
        rows_mock.data = [
            {"symbol_hl": "PEPE", "wallet_id": "w1", "reason": "unknown_symbol"},
            {"symbol_hl": "PEPE", "wallet_id": "w2", "reason": "unknown_symbol"},
            {"symbol_hl": "WIF",  "wallet_id": "w1", "reason": "unknown_symbol"},
            {"symbol_hl": "BTC",  "wallet_id": "w3", "reason": "below_min_size"},
        ]
        # query without reason filter goes through select().gte().execute()
        sb.table.return_value.select.return_value.gte.return_value.execute.return_value = rows_mock

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/skip-breakdown?hours=24&top=5")

        assert resp.status_code == 200
        data = resp.json()
        assert data["configured"] is True
        assert data["total"] == 4
        assert data["reason_filter"] is None
        assert data["window_hours"] == 24
        # PEPE has highest count (2)
        assert data["top_symbols"][0] == {"symbol_hl": "PEPE", "count": 2}
        # w1 has 2, others have 1 each
        assert data["top_wallets"][0] == {"wallet_id": "w1", "count": 2}

    def test_filter_by_reason_chains_eq(self, client):
        sb = MagicMock()
        rows_mock = MagicMock()
        rows_mock.data = [
            {"symbol_hl": "FARTCOIN", "wallet_id": "w9", "reason": "unknown_symbol"},
        ]
        # When reason filter is provided, .eq() is chained after .gte()
        chain = sb.table.return_value.select.return_value.gte.return_value
        chain.eq.return_value.execute.return_value = rows_mock

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/skip-breakdown?reason=unknown_symbol")

        assert resp.status_code == 200
        data = resp.json()
        assert data["reason_filter"] == "unknown_symbol"
        assert data["total"] == 1
        assert data["top_symbols"][0]["symbol_hl"] == "FARTCOIN"
        # Verify .eq was called with the right reason
        chain.eq.assert_called_once_with("reason", "unknown_symbol")

    def test_handles_null_symbol_or_wallet(self, client):
        sb = MagicMock()
        rows_mock = MagicMock()
        rows_mock.data = [
            {"symbol_hl": None, "wallet_id": None, "reason": "unknown_symbol"},
        ]
        sb.table.return_value.select.return_value.gte.return_value.execute.return_value = rows_mock

        with patch("src.routers.smart_money.get_supabase", return_value=sb):
            resp = client.get("/api/smart-money/skip-breakdown")

        assert resp.status_code == 200
        data = resp.json()
        assert data["top_symbols"][0]["symbol_hl"] == "(null)"
        assert data["top_wallets"][0]["wallet_id"] == "(null)"
