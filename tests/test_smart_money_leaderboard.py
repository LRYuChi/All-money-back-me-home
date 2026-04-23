"""Tests for scanner/leaderboard.py + cli/seed.py (offline).

Uses synthetic JSON matching HL's actual schema, so no network needed.
"""
from __future__ import annotations

import json

import pytest

from smart_money.scanner.leaderboard import (
    DEFAULT_STRATEGY,
    LeaderboardRow,
    SeedBucket,
    _parse_row,
    build_seed_set,
    fetch_leaderboard,
    filter_active,
    top_by,
)


# ------------------------------------------------------------------ #
# Synthetic fixtures (match the real HL schema exactly)
# ------------------------------------------------------------------ #
def _make_raw_row(addr: str, *, account: float = 50_000,
                  month_pnl: float = 1000, month_roi: float = 0.02,
                  month_vol: float = 200_000,
                  alltime_pnl: float = 10_000) -> dict:
    return {
        "ethAddress": addr,
        "accountValue": str(account),
        "displayName": None,
        "prize": 0,
        "windowPerformances": [
            ["day",     {"pnl": "0", "roi": "0", "vlm": "10000"}],
            ["week",    {"pnl": "300", "roi": "0.005", "vlm": "50000"}],
            ["month",   {"pnl": str(month_pnl), "roi": str(month_roi), "vlm": str(month_vol)}],
            ["allTime", {"pnl": str(alltime_pnl), "roi": "0.5", "vlm": "5000000"}],
        ],
    }


def _addr(letter: str, num: int) -> str:
    """Produce a valid 40-hex EVM-looking address for tests."""
    return f"0x{letter * 39}{num}"


A1 = _addr("a", 1)
A2 = _addr("a", 2)
A3 = _addr("a", 3)
B1 = _addr("b", 1)
B2 = _addr("b", 2)
C1 = _addr("c", 1)
C2 = _addr("c", 2)
D1 = _addr("d", 1)
D2 = _addr("d", 2)
E1 = _addr("e", 1)


@pytest.fixture
def sample_rows():
    # 10 synthetic rows varying across multiple dimensions
    return [
        # 3 big winners (match multiple buckets)
        _make_raw_row(A1, account=5_000_000, month_pnl=800_000, month_roi=0.16, alltime_pnl=30_000_000),
        _make_raw_row(A2, account=2_000_000, month_pnl=500_000, month_roi=0.25, alltime_pnl=10_000_000),
        _make_raw_row(A3, account=1_000_000, month_pnl=200_000, month_roi=0.20, alltime_pnl=5_000_000),
        # 2 ROI specialists (small account, high %)
        _make_raw_row(B1, account=50_000, month_pnl=20_000, month_roi=0.40, alltime_pnl=100_000),
        _make_raw_row(B2, account=30_000, month_pnl=10_000, month_roi=0.33, alltime_pnl=80_000),
        # 2 mediocre
        _make_raw_row(C1, account=500_000, month_pnl=5_000, month_roi=0.01),
        _make_raw_row(C2, account=300_000, month_pnl=3_000, month_roi=0.01),
        # Small / inactive (should be filtered)
        _make_raw_row(D1, account=500, month_pnl=50, month_roi=0.10, month_vol=5_000),
        # Volume zero (should be filtered)
        _make_raw_row(D2, account=100_000, month_pnl=0, month_roi=0, month_vol=0),
        # Negative month PnL but large allTime (losing streak, should still appear in allTime top)
        _make_raw_row(E1, account=800_000, month_pnl=-50_000, month_roi=-0.06, alltime_pnl=8_000_000),
    ]


# ------------------------------------------------------------------ #
# _parse_row
# ------------------------------------------------------------------ #
def test_parse_row_basic(sample_rows):
    r = _parse_row(sample_rows[0])
    assert r.address == A1
    assert r.account_value == 5_000_000
    assert r.get("month", "pnl") == 800_000
    assert r.get("month", "roi") == pytest.approx(0.16)


def test_parse_row_lowercases_address():
    row = _make_raw_row("0x" + "A" * 40)
    assert _parse_row(row).address == "0x" + "a" * 40


def test_parse_row_handles_missing_pnl():
    raw = _make_raw_row(A1)
    # remove week performance entirely
    raw["windowPerformances"] = [p for p in raw["windowPerformances"] if p[0] != "week"]
    r = _parse_row(raw)
    assert r.get("week", "pnl") == 0.0


# ------------------------------------------------------------------ #
# filter_active
# ------------------------------------------------------------------ #
def test_filter_active_removes_small_accounts(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    active = filter_active(rows, min_account_value=10_000, min_month_volume=10_000,
                            min_month_pnl_abs=100)
    addrs = {r.address for r in active}
    assert D1 not in addrs   # tiny account
    assert A1 in addrs


def test_filter_active_removes_zero_volume(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    active = filter_active(rows, min_month_volume=1_000)
    assert D2 not in {r.address for r in active}


def test_filter_active_keeps_negative_pnl_if_abs_large_enough(sample_rows):
    """|month_pnl| is 50k for E1 → should pass min_month_pnl_abs=1000."""
    rows = [_parse_row(r) for r in sample_rows]
    active = filter_active(rows, min_account_value=10_000, min_month_pnl_abs=1_000)
    assert E1 in {r.address for r in active}


# ------------------------------------------------------------------ #
# top_by
# ------------------------------------------------------------------ #
def test_top_by_month_pnl(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    top = top_by(rows, window="month", metric="pnl", n=3)
    addrs = [r.address for r in top]
    assert addrs == [A1, A2, A3]


def test_top_by_month_roi(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    top = top_by(rows, window="month", metric="roi", n=3)
    addrs = [r.address for r in top]
    # B1 highest ROI 0.40, B2=0.33, A2=0.25
    assert addrs == [B1, B2, A2]


def test_top_by_allTime_pnl(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    top = top_by(rows, window="allTime", metric="pnl", n=2)
    assert [r.address for r in top] == [A1, A2]


# ------------------------------------------------------------------ #
# build_seed_set
# ------------------------------------------------------------------ #
def test_build_seed_set_dedups_addresses(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    strategy = (
        SeedBucket("month", "pnl", 3, "mo_pnl"),
        SeedBucket("allTime", "pnl", 3, "all_pnl"),
    )
    seeds = build_seed_set(rows, strategy=strategy)
    addrs = [row.address for row, _ in seeds]
    # 不應有重複
    assert len(addrs) == len(set(addrs))


def test_build_seed_set_tracks_multi_hits(sample_rows):
    """命中多 bucket 的地址應有多個 tag."""
    rows = [_parse_row(r) for r in sample_rows]
    strategy = (
        SeedBucket("month", "pnl", 3, "mo_pnl"),     # picks a1, a2, a3
        SeedBucket("allTime", "pnl", 3, "all_pnl"),  # picks a1, a2, e1
    )
    seeds = build_seed_set(rows, strategy=strategy)
    tag_map = {row.address: tags for row, tags in seeds}
    assert "mo_pnl" in tag_map[A1] and "all_pnl" in tag_map[A1]
    assert len(tag_map[A1]) == 2
    assert len(tag_map[E1]) == 1


def test_build_seed_set_sorts_by_hit_count(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    strategy = (
        SeedBucket("month", "pnl", 3, "mo_pnl"),
        SeedBucket("allTime", "pnl", 3, "all_pnl"),
    )
    seeds = build_seed_set(rows, strategy=strategy)
    # 第一個地址的 tag 數必 ≥ 第二個
    hit_counts = [len(tags) for _, tags in seeds]
    assert hit_counts == sorted(hit_counts, reverse=True)


def test_default_strategy_produces_reasonable_count(sample_rows):
    rows = [_parse_row(r) for r in sample_rows]
    seeds = build_seed_set(rows, strategy=DEFAULT_STRATEGY)
    # 不應超過所有 input 數量
    assert 0 < len(seeds) <= len(rows)


# ------------------------------------------------------------------ #
# fetch_leaderboard (via cache)
# ------------------------------------------------------------------ #
def test_fetch_leaderboard_from_cache(tmp_path, sample_rows):
    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))
    rows = fetch_leaderboard(cache_path=cache, use_cache=True)
    assert len(rows) == len(sample_rows)
    assert all(isinstance(r, LeaderboardRow) for r in rows)


def test_fetch_leaderboard_cache_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        fetch_leaderboard(cache_path=tmp_path / "missing.json", use_cache=True)


# ------------------------------------------------------------------ #
# cli/seed.py
# ------------------------------------------------------------------ #
def test_cli_seed_dry_run_with_cache(tmp_path, sample_rows, capsys, monkeypatch):
    from smart_money.cli.seed import main

    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))
    out = tmp_path / "seeds.yaml"

    rc = main([
        "--cache", str(cache), "--use-cache",
        "--output", str(out),
        "--min-account-value", "10000",
        "--min-month-volume", "10000",
        "--dry-run",
        "--only", "month:pnl:5",
        "--log-level", "WARNING",   # keep output clean
    ])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Dry-run" in captured
    # No file written in dry-run
    assert not out.exists()


def test_cli_seed_writes_yaml(tmp_path, sample_rows):
    import yaml
    from smart_money.cli.seed import main

    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))
    out = tmp_path / "seeds.yaml"

    rc = main([
        "--cache", str(cache), "--use-cache",
        "--output", str(out),
        "--min-account-value", "10000",
        "--min-month-volume", "10000",
        "--only", "month:pnl:3",
        "--only", "month:roi:3",
        "--log-level", "WARNING",
    ])
    assert rc == 0
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    wallets = data["wallets"]
    assert len(wallets) > 0
    # All entries must have address + tags + account_value_usd
    for w in wallets:
        assert "address" in w
        assert "tags" in w
        assert w["account_value_usd"] > 0


def test_cli_seed_merge_mode_preserves_existing(tmp_path, sample_rows):
    import yaml
    from smart_money.cli.seed import main

    # Pre-populate seeds.yaml with a pre-existing wallet
    existing = tmp_path / "seeds.yaml"
    existing.write_text(yaml.safe_dump({"wallets": [
        {"address": "0xdeadbeef0000000000000000000000000000dead", "tags": ["manual"]},
    ]}))

    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))

    rc = main([
        "--cache", str(cache), "--use-cache",
        "--output", str(existing),
        "--merge",
        "--min-account-value", "10000",
        "--min-month-volume", "10000",
        "--only", "month:pnl:3",
        "--log-level", "WARNING",
    ])
    assert rc == 0

    data = yaml.safe_load(existing.read_text())
    addrs = [w["address"] if isinstance(w, dict) else w for w in data["wallets"]]
    assert "0xdeadbeef0000000000000000000000000000dead" in addrs
    assert len(addrs) > 1


def test_cli_seed_rejects_bad_spec(tmp_path, sample_rows):
    from smart_money.cli.seed import main

    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))

    with pytest.raises(SystemExit):
        main([
            "--cache", str(cache), "--use-cache",
            "--only", "badformat",
            "--dry-run",
            "--log-level", "WARNING",
        ])


def test_written_yaml_is_loadable_by_existing_seed_loader(tmp_path, sample_rows):
    """由 cli/seed 寫出來的 yaml,必須能被 scanner/seeds.py load."""
    from smart_money.cli.seed import main
    from smart_money.scanner.seeds import load_seed_file

    cache = tmp_path / "lb.json"
    cache.write_text(json.dumps({"leaderboardRows": sample_rows}))
    out = tmp_path / "seeds.yaml"

    main([
        "--cache", str(cache), "--use-cache",
        "--output", str(out),
        "--min-account-value", "10000",
        "--min-month-volume", "10000",
        "--only", "month:pnl:3",
        "--log-level", "WARNING",
    ])

    # The existing seeds loader must parse this file cleanly
    addrs = load_seed_file(out)
    assert len(addrs) >= 1
    # All should be lowercase
    assert all(a == a.lower() for a in addrs)
