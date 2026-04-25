"""Tests for smart_money/cli/whale_analyzer.py — R65.

Covers the analyze() pure function, hedge calc, classifier, and HTML
template rendering. Network calls are not exercised — fills are
synthesized to match HL Info API shape.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from smart_money.cli.whale_analyzer import (
    Analysis,
    analyze,
    classify_strategy,
    generate_html,
    hedged_market_pct,
    print_report,
)


def _mk_fill(
    *, ts_ms: int, coin: str = "BTC", direction: str = "Open Long",
    sz: float = 1.0, px: float = 50000.0, pnl: float | None = None,
    fee: float = 0.5,
) -> dict:
    """Build a synthetic HL fill dict matching the Info API shape."""
    f = {
        "time": ts_ms,
        "coin": coin,
        "dir": direction,
        "sz": sz,
        "px": px,
        "fee": fee,
    }
    if pnl is not None:
        f["closedPnl"] = pnl
    return f


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# =================================================================== #
# Empty / edge cases
# =================================================================== #
def test_analyze_empty_fills_returns_default():
    a = analyze([])
    assert a.n_fills == 0
    assert a.total_pnl_usd == 0
    assert a.win_rate == 0
    assert a.sorted_fills == []


def test_analyze_unparseable_dir_counts_as_other():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [_mk_fill(ts_ms=_ms(base), direction="Buy")]
    a = analyze(fills)
    assert a.direction_count.get("other", 0) == 1
    assert a.n_open == 0


# =================================================================== #
# Counting / aggregation
# =================================================================== #
def test_analyze_aggregates_volume_and_pnl():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long",
                 coin="BTC", sz=1, px=50_000, fee=10),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=1)),
                 direction="Close Long", coin="BTC",
                 sz=1, px=51_000, pnl=1000, fee=10),
    ]
    a = analyze(fills)
    # 1×50k + 1×51k = 101k volume
    assert a.total_volume_usd == 101_000
    assert a.total_pnl_usd == 1000
    assert a.total_fee_usd == 20
    assert a.n_winners == 1
    assert a.n_losers == 0
    assert a.win_rate == 1.0


def test_analyze_separates_long_and_short_opens():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=30)),
                 direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=60)),
                 direction="Open Short"),
    ]
    a = analyze(fills)
    assert a.n_long_open == 2
    assert a.n_short_open == 1
    assert a.n_open == 3
    assert a.n_close == 0


def test_analyze_hold_duration_pairs_fifo():
    """First close pops the FIRST open of (coin, side)."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=2)),
                 direction="Close Long", pnl=500),
    ]
    a = analyze(fills)
    assert len(a.hold_durations_h) == 1
    assert abs(a.hold_durations_h[0] - 2.0) < 0.01


def test_analyze_per_asset_breakdown():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), coin="BTC", direction="Open Long",
                 sz=1, px=50_000),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=30)), coin="ETH",
                 direction="Open Long", sz=10, px=2500),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=1)), coin="BTC",
                 direction="Close Long", sz=1, px=51_000, pnl=1000),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=2)), coin="ETH",
                 direction="Close Long", sz=10, px=2400, pnl=-1000),
    ]
    a = analyze(fills)
    assert a.asset_count["BTC"] == 2
    assert a.asset_count["ETH"] == 2
    assert a.asset_pnl["BTC"] == 1000
    assert a.asset_pnl["ETH"] == -1000


def test_analyze_avg_gap_excludes_long_pauses():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=60)),
                 direction="Open Long"),
        # 25h gap — must be excluded from avg
        _mk_fill(ts_ms=_ms(base + timedelta(hours=25, seconds=60)),
                 direction="Open Long"),
    ]
    a = analyze(fills)
    # Only the first 60s gap counts
    assert a.avg_gap_sec == 60.0


def test_analyze_size_buckets_classify_correctly():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Notionals: 50, 200, 7500, 250_000
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i)),
                 sz=sz, px=px, direction="Open Long")
        for i, (sz, px) in enumerate([
            (1, 50),       # 50 → bucket 0 (<100)
            (1, 200),      # 200 → bucket 1 (100-500)
            (1, 7500),     # 7500 → bucket 4 (5k-10k)
            (1, 250_000),  # 250k → bucket 7 (100k-500k)
        ])
    ]
    a = analyze(fills)
    assert a.size_buckets[0] == 1
    assert a.size_buckets[1] == 1
    assert a.size_buckets[4] == 1
    assert a.size_buckets[7] == 1


def test_analyze_hour_count_and_peak():
    base = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i * 30)),
                 direction="Open Long")
        for i in range(5)
    ]
    a = analyze(fills)
    assert a.hour_count[14] == 5
    assert a.peak_hour == 14


# =================================================================== #
# hedged_market_pct
# =================================================================== #
def test_hedge_pct_zero_when_no_hedging():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), coin="BTC", direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=30)),
                 coin="ETH", direction="Open Long"),
    ]
    a = analyze(fills)
    assert hedged_market_pct(a) == 0.0


def test_hedge_pct_full_when_all_hedged():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), coin="BTC", direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=10)),
                 coin="BTC", direction="Open Short"),
    ]
    a = analyze(fills)
    assert hedged_market_pct(a) == 100.0


def test_hedge_pct_partial():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), coin="BTC", direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=10)),
                 coin="BTC", direction="Open Short"),
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=20)),
                 coin="ETH", direction="Open Long"),
    ]
    a = analyze(fills)
    # 1 of 2 hedged
    assert hedged_market_pct(a) == 50.0


# =================================================================== #
# classify_strategy
# =================================================================== #
def test_classify_high_freq_bot():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 10 fills at 5s gaps = avg_gap 5s → bot
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i * 5)),
                 direction="Open Long")
        for i in range(10)
    ]
    a = analyze(fills)
    label, _ = classify_strategy(a)
    assert "BOT" in label


def test_classify_long_short_bias_detected():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i * 60)),
                 direction="Open Long")
        for i in range(10)
    ]
    a = analyze(fills)
    label, _ = classify_strategy(a)
    assert "偏多" in label


def test_classify_swing_horizon():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=24)),
                 direction="Close Long", pnl=100),
    ]
    a = analyze(fills)
    label, _ = classify_strategy(a)
    assert "swing" in label


def test_classify_high_winrate_high_pf_verdict():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = []
    # 7 winners 100, 3 losers -20 → win_rate 70%, PF (700)/(60) ≈ 11.6
    for i, pnl in enumerate([100] * 7 + [-20] * 3):
        fills.append(_mk_fill(
            ts_ms=_ms(base + timedelta(minutes=i * 2)),
            direction="Open Long",
        ))
        fills.append(_mk_fill(
            ts_ms=_ms(base + timedelta(minutes=i * 2 + 1)),
            direction="Close Long", pnl=pnl,
        ))
    a = analyze(fills)
    _, verdict = classify_strategy(a)
    assert "真鯨魚" in verdict


def test_classify_losing_verdict():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = []
    for i, pnl in enumerate([-50, -50, -50, 10]):
        fills.append(_mk_fill(
            ts_ms=_ms(base + timedelta(minutes=i * 2)),
            direction="Open Long",
        ))
        fills.append(_mk_fill(
            ts_ms=_ms(base + timedelta(minutes=i * 2 + 1)),
            direction="Close Long", pnl=pnl,
        ))
    a = analyze(fills)
    _, verdict = classify_strategy(a)
    assert "虧損" in verdict or "不建議" in verdict


# =================================================================== #
# HTML rendering
# =================================================================== #
def test_generate_html_contains_address_and_stats():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base), direction="Open Long"),
        _mk_fill(ts_ms=_ms(base + timedelta(hours=1)),
                 direction="Close Long", pnl=42),
    ]
    a = analyze(fills)
    addr = "0x" + "a" * 40
    html = generate_html(addr, a, state=None)
    assert addr in html
    assert "WHALE" in html
    assert "stats_json" not in html   # placeholder must be substituted
    assert '"n_fills": 2' in html
    # css braces survived double-brace escaping
    assert "background:var(--bg)" in html


def test_generate_html_handles_no_pnl_data():
    """Wallet with only opens (no closes) — no win-rate/PF data."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i * 30)),
                 direction="Open Long")
        for i in range(5)
    ]
    a = analyze(fills)
    html = generate_html("0x" + "b" * 40, a, state=None)
    # Must not crash; should contain win_rate=0
    assert '"win_rate": 0' in html


# =================================================================== #
# print_report (smoke — just verify no exceptions)
# =================================================================== #
def test_print_report_smoke(capsys):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fills = [
        _mk_fill(ts_ms=_ms(base + timedelta(seconds=i * 60)),
                 direction="Open Long" if i % 2 == 0 else "Close Long",
                 pnl=10 if i % 2 == 1 else None)
        for i in range(6)
    ]
    a = analyze(fills)
    print_report("0x" + "c" * 40, a, state=None)
    captured = capsys.readouterr()
    assert "HYPERLIQUID WHALE ANALYSIS" in captured.out
    assert "Win rate" in captured.out
