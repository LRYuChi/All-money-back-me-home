"""Tests for smart_money.signals.whitelist (P4b)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from smart_money.signals.whitelist import (
    WhitelistOverride,
    build_whitelist,
    load_manual_override,
)
from smart_money.store.db import InMemoryStore
from smart_money.store.schema import Ranking


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
@pytest.fixture
def now() -> datetime:
    return datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore()


def seed_ranked_wallets(store: InMemoryStore, count: int, *, as_of: datetime, snapshot_date: datetime):
    """Create `count` wallets, tag them active, and rank them 1..count."""
    rankings = []
    wallets = []
    for i in range(count):
        addr = f"0x{i:040x}"
        w = store.upsert_wallet(addr, seen_at=as_of - timedelta(days=60))
        # Simulate recent activity
        w.last_active_at = as_of - timedelta(hours=1)
        wallets.append(w)
        rankings.append(Ranking(
            snapshot_date=snapshot_date,
            wallet_id=w.id,
            rank=i + 1,
            score=1.0 - i * 0.05,
            metrics={},
        ))
    store.save_ranking(rankings)
    return wallets


# ------------------------------------------------------------------ #
# load_manual_override
# ------------------------------------------------------------------ #
def test_load_manual_override_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "nothere.yaml"
    ov = load_manual_override(missing)
    assert ov.include == set()
    assert ov.exclude == set()


def test_load_manual_override_none_path_returns_empty():
    ov = load_manual_override(None)
    assert ov.include == set() and ov.exclude == set()


def test_load_manual_override_parses_yaml(tmp_path):
    f = tmp_path / "override.yaml"
    f.write_text("include:\n  - '0xAAA'\n  - '0xBBB'\nexclude:\n  - '0xCCC'\n")
    ov = load_manual_override(f)
    assert ov.include == {"0xaaa", "0xbbb"}
    assert ov.exclude == {"0xccc"}


def test_load_manual_override_tolerates_malformed_yaml(tmp_path):
    """Bad YAML should not crash the daemon — log warning and return empty."""
    f = tmp_path / "bad.yaml"
    f.write_text("include: [\nunclosed")
    ov = load_manual_override(f)
    assert ov.include == set()


# ------------------------------------------------------------------ #
# build_whitelist — ranking-sourced
# ------------------------------------------------------------------ #
def test_no_rankings_returns_empty(store, now):
    result = build_whitelist(store, as_of=now, whitelist_size=10)
    assert result == []


def test_top_n_ranked_wallets_returned(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    seed_ranked_wallets(store, count=15, as_of=now, snapshot_date=snapshot)

    result = build_whitelist(store, as_of=now, whitelist_size=10)

    assert len(result) == 10
    # Sorted by rank 1..10
    assert [e.rank for e in result] == list(range(1, 11))
    assert all(e.is_tradeable for e in result)
    assert all(e.source == "ranking" for e in result)


def test_whitelist_size_respected(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    seed_ranked_wallets(store, count=20, as_of=now, snapshot_date=snapshot)

    result = build_whitelist(store, as_of=now, whitelist_size=5)
    assert len(result) == 5


# ------------------------------------------------------------------ #
# Freshness filter
# ------------------------------------------------------------------ #
def test_stale_wallet_is_demoted_not_removed(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=3, as_of=now, snapshot_date=snapshot)

    # Make rank-2 stale
    wallets[1].last_active_at = now - timedelta(days=30)

    result = build_whitelist(store, as_of=now, whitelist_size=3, freshness_days=14)
    assert len(result) == 3
    # rank=1 and rank=3 tradeable, rank=2 demoted
    by_rank = {e.rank: e for e in result}
    assert by_rank[1].is_tradeable is True
    assert by_rank[2].is_tradeable is False
    assert by_rank[2].demotion_reason == "stale_no_fills"
    assert by_rank[3].is_tradeable is True


# ------------------------------------------------------------------ #
# Manual include / exclude
# ------------------------------------------------------------------ #
def test_manual_exclude_demotes_ranked_wallet(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=3, as_of=now, snapshot_date=snapshot)

    override = WhitelistOverride(include=set(), exclude={wallets[0].address.lower()})
    result = build_whitelist(store, as_of=now, whitelist_size=3, override=override)

    by_rank = {e.rank: e for e in result}
    assert by_rank[1].is_tradeable is False
    assert by_rank[1].demotion_reason == "manual_exclude"
    # Ranks 2 and 3 untouched
    assert by_rank[2].is_tradeable is True


def test_manual_include_adds_non_ranked_wallet(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    seed_ranked_wallets(store, count=3, as_of=now, snapshot_date=snapshot)

    # A wallet that's in sm_wallets but NOT in top 3
    extra = store.upsert_wallet("0xMANUAL_ADD", seen_at=now - timedelta(days=5))
    extra.last_active_at = now - timedelta(minutes=10)

    override = WhitelistOverride(include={"0xmanual_add"}, exclude=set())
    result = build_whitelist(store, as_of=now, whitelist_size=3, override=override)

    # 3 ranking + 1 manual
    assert len(result) == 4
    manual = [e for e in result if e.source == "manual_include"]
    assert len(manual) == 1
    assert manual[0].rank is None
    assert manual[0].is_tradeable


def test_manual_include_not_in_wallets_is_skipped(store, now):
    """Include for an address we don't have wallet record for — skip (warn)."""
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    seed_ranked_wallets(store, count=2, as_of=now, snapshot_date=snapshot)

    override = WhitelistOverride(include={"0xnotinwalletstable"}, exclude=set())
    result = build_whitelist(store, as_of=now, whitelist_size=2, override=override)

    # Only the 2 ranked — manual include silently dropped
    assert len(result) == 2


def test_manual_include_duplicate_of_ranked_keeps_ranking_entry(store, now):
    """If a manually-included address is already top-N, don't double-count."""
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=3, as_of=now, snapshot_date=snapshot)

    override = WhitelistOverride(include={wallets[0].address.lower()}, exclude=set())
    result = build_whitelist(store, as_of=now, whitelist_size=3, override=override)

    assert len(result) == 3  # not 4
    # The duplicate retained ranking source
    assert result[0].source == "ranking"


def test_exclude_overrides_include(store, now):
    """Edge case: address in both include and exclude lists → exclude wins."""
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=2, as_of=now, snapshot_date=snapshot)

    addr = wallets[0].address.lower()
    override = WhitelistOverride(include={addr}, exclude={addr})
    result = build_whitelist(store, as_of=now, whitelist_size=2, override=override)

    by_rank = {e.rank: e for e in result}
    assert by_rank[1].is_tradeable is False
    assert by_rank[1].demotion_reason == "manual_exclude"


# ------------------------------------------------------------------ #
# Warmup
# ------------------------------------------------------------------ #
def test_warmup_demotes_newly_seen_wallets(store, now):
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=3, as_of=now, snapshot_date=snapshot)

    # Make wallet[2] brand new
    wallets[2].first_seen_at = now - timedelta(hours=12)

    warmup_cutoff = now - timedelta(hours=48)
    result = build_whitelist(
        store, as_of=now, whitelist_size=3, warmup_cutoff=warmup_cutoff,
    )

    by_rank = {e.rank: e for e in result}
    assert by_rank[3].is_tradeable is False
    assert by_rank[3].demotion_reason == "warmup"
    # Old wallets unaffected
    assert by_rank[1].is_tradeable is True
    assert by_rank[2].is_tradeable is True


def test_manual_exclude_precedes_stale(store, now):
    """Priority test: when both conditions apply, demotion_reason should be
    manual_exclude (not stale)."""
    snapshot = datetime(2026, 4, 22, tzinfo=timezone.utc)
    wallets = seed_ranked_wallets(store, count=1, as_of=now, snapshot_date=snapshot)

    # Make it both stale AND excluded
    wallets[0].last_active_at = now - timedelta(days=30)
    override = WhitelistOverride(include=set(), exclude={wallets[0].address.lower()})

    result = build_whitelist(
        store, as_of=now, whitelist_size=1, override=override, freshness_days=14,
    )
    assert result[0].demotion_reason == "manual_exclude"
