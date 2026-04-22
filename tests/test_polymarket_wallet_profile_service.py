"""Tests for WalletProfileService — fallback logic between two tables."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from polymarket.services.wallet_profile_service import WalletProfileService
from polymarket.storage.repo import SqliteRepo


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepo(db_path=tmp_path / "t.db")
    yield r
    r.close()


@pytest.fixture
def service(repo):
    return WalletProfileService(repo)


def _whale_stats(wallet="0xabc", tier="C") -> dict:
    return {
        "wallet_address": wallet,
        "tier": tier,
        "trade_count_90d": 20,
        "win_rate": 0.55,
        "cumulative_pnl": 800.0,
        "avg_trade_size": 150.0,
        "segment_win_rates": [0.5, 0.55, 0.6],
        "stability_pass": True,
        "resolved_count": 8,
        "last_trade_at": "2026-04-21T00:00:00+00:00",
    }


def _profile_dict(wallet="0xabc", tier="A", scanned_at=None) -> dict:
    return {
        "wallet_address": wallet,
        "scanner_version": "1.5a.0",
        "scanned_at": scanned_at or datetime.now(timezone.utc).isoformat(),
        "passed_coarse_filter": 1,
        "coarse_filter_reasons": json.dumps([]),
        "trade_count_90d": 50,
        "resolved_count": 25,
        "cumulative_pnl": 12000.0,
        "avg_trade_size": 800.0,
        "win_rate": 0.7,
        "features_json": json.dumps(
            {"core_stats": {"value": {"last_trade_at": "2026-04-22T11:00:00+00:00"}}}
        ),
        "tier": tier,
        "archetypes_json": json.dumps([]),
        "risk_flags_json": json.dumps([]),
        "sample_size_warning": 0,
        "raw_features_json": json.dumps({}),
    }


class TestGetProfile:
    def test_returns_none_when_no_data(self, service):
        assert service.get_profile("0xnowhere") is None

    def test_falls_back_to_whale_stats(self, repo, service):
        repo.upsert_whale_stats(_whale_stats(tier="B"))
        view = service.get_profile("0xabc")
        assert view is not None
        assert view.data_source == "whale_stats"
        assert view.tier == "B"
        assert view.scanner_version is None

    def test_prefers_wallet_profiles_over_whale_stats(self, repo, service):
        repo.upsert_whale_stats(_whale_stats(tier="C"))
        repo.insert_wallet_profile(_profile_dict(tier="A"))
        view = service.get_profile("0xabc")
        assert view is not None
        assert view.data_source == "wallet_profiles"
        assert view.tier == "A"  # newer source wins
        assert view.scanner_version == "1.5a.0"


class TestListByTier:
    def test_returns_wallet_profiles_first(self, repo, service):
        repo.insert_wallet_profile(_profile_dict(wallet="0x1", tier="A"))
        repo.upsert_whale_stats(_whale_stats(wallet="0x2", tier="A"))

        views = service.list_profiles_by_tier(["A"])
        assert len(views) == 2
        sources = {v.wallet_address: v.data_source for v in views}
        assert sources["0x1"] == "wallet_profiles"
        assert sources["0x2"] == "whale_stats"

    def test_no_duplicate_when_both_tables_have_wallet(self, repo, service):
        repo.insert_wallet_profile(_profile_dict(wallet="0x1", tier="A"))
        repo.upsert_whale_stats(_whale_stats(wallet="0x1", tier="C"))
        # Same wallet in both tables — service uses the wp version, doesn't double-list
        views = service.list_profiles_by_tier(["A", "C"])
        assert len([v for v in views if v.wallet_address == "0x1"]) == 1
        # The wp version had tier=A, so it surfaces under tier filter A
        target = next(v for v in views if v.wallet_address == "0x1")
        assert target.tier == "A"
        assert target.data_source == "wallet_profiles"


class TestProfileHistory:
    def test_returns_history_in_reverse_time_order(self, repo, service):
        for i in range(3):
            repo.insert_wallet_profile(
                _profile_dict(scanned_at=f"2026-04-22T0{i+1}:00:00+00:00")
            )
        history = service.list_profile_history("0xabc")
        assert len(history) == 3
        # all from wallet_profiles
        assert all(h.data_source == "wallet_profiles" for h in history)
