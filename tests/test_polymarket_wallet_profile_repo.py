"""Tests for repo methods around wallet_profiles (append-only time series)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from polymarket.storage.repo import SqliteRepo


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepo(db_path=tmp_path / "t.db")
    yield r
    r.close()


def _profile_dict(
    wallet: str = "0xabc",
    version: str = "1.5a.0",
    tier: str = "C",
    scanned_at: str | None = None,
) -> dict:
    return {
        "wallet_address": wallet,
        "scanner_version": version,
        "scanned_at": scanned_at or datetime.now(timezone.utc).isoformat(),
        "passed_coarse_filter": 1,
        "coarse_filter_reasons": json.dumps([]),
        "trade_count_90d": 25,
        "resolved_count": 10,
        "cumulative_pnl": 1500.0,
        "avg_trade_size": 200.0,
        "win_rate": 0.6,
        "features_json": json.dumps({"core_stats": {"value": {"x": 1}, "confidence": "ok"}}),
        "tier": tier,
        "archetypes_json": json.dumps([]),
        "risk_flags_json": json.dumps([]),
        "sample_size_warning": 0,
        "raw_features_json": json.dumps({}),
    }


class TestInsertAndQueryProfiles:
    def test_insert_returns_id(self, repo):
        rid = repo.insert_wallet_profile(_profile_dict())
        assert rid > 0
        assert repo.count_wallet_profiles() == 1

    def test_append_only_no_overwrite(self, repo):
        repo.insert_wallet_profile(_profile_dict(scanned_at="2026-04-22T01:00:00+00:00"))
        repo.insert_wallet_profile(_profile_dict(scanned_at="2026-04-22T02:00:00+00:00"))
        assert repo.count_wallet_profiles() == 2

    def test_get_latest_returns_newest(self, repo):
        repo.insert_wallet_profile(
            _profile_dict(scanned_at="2026-04-22T01:00:00+00:00", tier="C")
        )
        repo.insert_wallet_profile(
            _profile_dict(scanned_at="2026-04-22T02:00:00+00:00", tier="A")
        )
        latest = repo.get_latest_wallet_profile("0xabc")
        assert latest is not None
        assert latest["tier"] == "A"

    def test_get_latest_filters_by_version(self, repo):
        repo.insert_wallet_profile(
            _profile_dict(version="1.5a.0", scanned_at="2026-04-22T01:00:00+00:00", tier="C")
        )
        repo.insert_wallet_profile(
            _profile_dict(version="1.5b.0", scanned_at="2026-04-22T02:00:00+00:00", tier="A")
        )
        v1 = repo.get_latest_wallet_profile("0xabc", scanner_version="1.5a.0")
        assert v1 is not None
        assert v1["tier"] == "C"

    def test_list_latest_one_per_wallet(self, repo):
        # Two scans for w1, one scan for w2
        repo.insert_wallet_profile(
            _profile_dict(wallet="0x1", scanned_at="2026-04-22T01:00:00+00:00", tier="A")
        )
        repo.insert_wallet_profile(
            _profile_dict(wallet="0x1", scanned_at="2026-04-22T02:00:00+00:00", tier="B")
        )
        repo.insert_wallet_profile(
            _profile_dict(wallet="0x2", scanned_at="2026-04-22T01:00:00+00:00", tier="C")
        )
        profiles = repo.list_latest_wallet_profiles()
        addrs = [p["wallet_address"] for p in profiles]
        assert addrs.count("0x1") == 1
        assert addrs.count("0x2") == 1
        # w1's latest should be the B-tier one
        w1 = next(p for p in profiles if p["wallet_address"] == "0x1")
        assert w1["tier"] == "B"

    def test_list_latest_filters_by_tier(self, repo):
        repo.insert_wallet_profile(
            _profile_dict(wallet="0x1", tier="A", scanned_at="2026-04-22T01:00:00+00:00")
        )
        repo.insert_wallet_profile(
            _profile_dict(wallet="0x2", tier="C", scanned_at="2026-04-22T01:00:00+00:00")
        )
        a_only = repo.list_latest_wallet_profiles(tier="A")
        assert len(a_only) == 1
        assert a_only[0]["wallet_address"] == "0x1"

    def test_list_history(self, repo):
        for i in range(5):
            repo.insert_wallet_profile(
                _profile_dict(scanned_at=f"2026-04-22T0{i+1}:00:00+00:00", tier="C")
            )
        history = repo.list_wallet_profile_history("0xabc", limit=10)
        assert len(history) == 5
        # newest first
        assert history[0]["scanned_at"] > history[-1]["scanned_at"]
