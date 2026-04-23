"""Tests for polymarket router — wallet detail endpoint (Phase A dashboard).

Strategy: seed a temp SQLite DB with the same schema as production, point
`src.routers.polymarket._DB_PATH` at it, then hit the endpoint via TestClient.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


SCHEMA = """
CREATE TABLE markets (
    condition_id TEXT PRIMARY KEY, question TEXT, market_slug TEXT, category TEXT,
    end_date_iso TEXT, active INTEGER, closed INTEGER, minimum_order_size REAL,
    minimum_tick_size REAL, maker_base_fee REAL, taker_base_fee REAL,
    raw_json TEXT, fetched_at TEXT, updated_at TEXT
);
CREATE TABLE trades (
    id TEXT PRIMARY KEY, condition_id TEXT, token_id TEXT, price REAL, size REAL,
    notional REAL, side TEXT, status TEXT, maker_address TEXT, taker_address TEXT,
    match_time TEXT, raw_json TEXT, fetched_at TEXT
);
CREATE TABLE whale_stats (
    wallet_address TEXT PRIMARY KEY, tier TEXT, trade_count_90d INTEGER,
    win_rate REAL, cumulative_pnl REAL, avg_trade_size REAL, segment_win_rates TEXT,
    stability_pass INTEGER, resolved_count INTEGER, last_trade_at TEXT, last_computed_at TEXT
);
CREATE TABLE whale_tier_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_address TEXT, from_tier TEXT,
    to_tier TEXT, changed_at TEXT, reason TEXT
);
CREATE TABLE wallet_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_address TEXT, scanner_version TEXT,
    scanned_at TEXT, passed_coarse_filter INTEGER, coarse_filter_reasons TEXT,
    trade_count_90d INTEGER, resolved_count INTEGER, cumulative_pnl REAL,
    avg_trade_size REAL, win_rate REAL, features_json TEXT, tier TEXT,
    archetypes_json TEXT, risk_flags_json TEXT, sample_size_warning INTEGER,
    raw_features_json TEXT
);
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Set up a temp SQLite DB + patched router module + TestClient."""
    db_path = tmp_path / "polymarket.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    # Import and monkey-patch the module's DB path
    from src.routers import polymarket as pm_router
    monkeypatch.setattr(pm_router, "_DB_PATH", db_path)
    pm_router._cache.clear()  # wipe any pre-existing cache

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(pm_router.router)
    return TestClient(app), db_path


def _seed_wallet(
    db_path: Path,
    address: str,
    *,
    tier: str = "A",
    with_curve: bool = True,
    resolved_count: int = 30,
):
    """Insert a minimal wallet into whale_stats + wallet_profiles."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO whale_stats (wallet_address, tier, trade_count_90d, win_rate,
           cumulative_pnl, avg_trade_size, segment_win_rates, stability_pass,
           resolved_count, last_trade_at, last_computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (address, tier, 40, 0.68, 12000.0, 450.0, "[0.7, 0.65, 0.68]",
         1, resolved_count, "2026-04-20T10:00:00+00:00", "2026-04-23T00:00:00+00:00"),
    )
    features: dict = {
        "core_stats": {
            "feature_version": "1.0",
            "value": {"trade_count_90d": 40, "win_rate": 0.68, "cumulative_pnl": 12000.0},
            "confidence": "ok",
        }
    }
    if with_curve:
        features["steady_growth"] = {
            "feature_version": "1.1",
            "value": {
                "is_steady_grower": True,
                "smoothness_score": 0.85,
                "components": {"r_squared": 0.96, "gain_to_pain_ratio": 3.0, "gain_to_pain_normalized": 1.0, "new_high_frequency_30d": 0.6},
                "curve": [
                    {"date": "2026-02-01", "value": 0},
                    {"date": "2026-02-15", "value": 3000},
                    {"date": "2026-03-01", "value": 6500},
                    {"date": "2026-04-01", "value": 9500},
                    {"date": "2026-04-20", "value": 12000},
                ],
                "events": [
                    {"date": "2026-02-15", "pnl": 3000, "won": True, "notional": 5000, "condition_id": "0x1", "outcome": "Yes"},
                    {"date": "2026-04-01", "pnl": 3000, "won": True, "notional": 4000, "condition_id": "0x2", "outcome": "Yes"},
                ],
                "max_drawdown_ratio": 0.03,
                "longest_losing_streak": 2,
            },
            "confidence": "ok",
        }
    conn.execute(
        """INSERT INTO wallet_profiles (wallet_address, scanner_version, scanned_at,
           passed_coarse_filter, coarse_filter_reasons, trade_count_90d, resolved_count,
           cumulative_pnl, avg_trade_size, win_rate, features_json, tier,
           archetypes_json, risk_flags_json, sample_size_warning, raw_features_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (address, "1.5b.1", "2026-04-23T00:00:00+00:00", 1, "[]",
         40, resolved_count, 12000.0, 450.0, 0.68,
         json.dumps(features), tier, "[]", "[]", 0, "{}"),
    )
    conn.execute(
        """INSERT INTO whale_tier_history (wallet_address, from_tier, to_tier, changed_at, reason)
           VALUES (?, ?, ?, ?, ?)""",
        (address, None, tier, "2026-04-10T00:00:00+00:00", "initial"),
    )
    conn.commit()
    conn.close()


class TestWalletDetail:
    def test_404_when_not_found(self, client):
        c, _ = client
        resp = c.get("/api/polymarket/wallet/0xNONEXISTENT")
        assert resp.status_code == 404

    def test_returns_full_structure(self, client):
        c, db_path = client
        _seed_wallet(db_path, "0xABC123")
        resp = c.get("/api/polymarket/wallet/0xABC123")
        assert resp.status_code == 200
        data = resp.json()

        assert data["wallet_address"] == "0xABC123"
        assert data["stats"]["tier"] == "A"
        assert data["stats"]["win_rate"] == pytest.approx(0.68)
        assert data["stats"]["cumulative_pnl"] == pytest.approx(12000.0)
        assert data["scanner_version"] == "1.5b.1"

        # Features
        assert data["features"]["steady_growth"]["confidence"] == "ok"
        assert data["features"]["steady_growth"]["value"]["is_steady_grower"] is True

        # Curve + events (top-level convenience)
        assert len(data["curve"]) == 5
        assert data["curve"][0] == {"date": "2026-02-01", "value": 0}
        assert data["curve"][-1]["value"] == 12000

        assert len(data["events"]) == 2
        assert data["events"][0]["won"] is True

        # Tier history
        assert len(data["tier_history"]) == 1
        assert data["tier_history"][0]["to_tier"] == "A"

    def test_empty_curve_when_feature_missing(self, client):
        c, db_path = client
        _seed_wallet(db_path, "0xNOFEATURE", with_curve=False, resolved_count=3)
        resp = c.get("/api/polymarket/wallet/0xNOFEATURE")
        assert resp.status_code == 200
        data = resp.json()
        assert data["curve"] == []
        assert data["events"] == []

    def test_recent_trades_included(self, client):
        c, db_path = client
        _seed_wallet(db_path, "0xTRADER")
        conn = sqlite3.connect(db_path)
        conn.execute(
            """INSERT INTO markets (condition_id, question, category, active, closed, fetched_at, updated_at)
               VALUES (?, ?, ?, 1, 0, ?, ?)""",
            ("0x1", "Will X happen?", "Politics", "2026-04-20T00:00:00+00:00", "2026-04-20T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO trades (id, condition_id, token_id, price, size, notional, side,
               maker_address, taker_address, match_time, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("tx1:0", "0x1", "tok1", 0.45, 1000, 450, "BUY",
             "0xOTHER", "0xTRADER", "2026-04-22T10:00:00+00:00", "2026-04-22T10:00:00+00:00"),
        )
        conn.commit()
        conn.close()

        resp = c.get("/api/polymarket/wallet/0xTRADER")
        data = resp.json()
        assert len(data["recent_trades"]) == 1
        t = data["recent_trades"][0]
        assert t["condition_id"] == "0x1"
        assert t["market_question"] == "Will X happen?"
        assert t["market_category"] == "Politics"
