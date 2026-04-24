"""Tests for reflection.hl_price — HLPriceFetcher + helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from reflection.hl_price import (
    HLPriceFetcher,
    _parse_canonical,
    _pick_interval,
)
from reflection.price import PriceUnavailable


# ================================================================== #
# _parse_canonical
# ================================================================== #
@pytest.mark.parametrize("symbol,expected", [
    ("crypto:hyperliquid:BTC", "BTC"),
    ("crypto:hyperliquid:eth", "ETH"),       # case-normalised
    ("crypto:OKX:BTC/USDT:USDT", "BTC"),     # OKX with full symbol → first chunk
    ("crypto:hyperliquid:HYPE", "HYPE"),
])
def test_parse_canonical_extracts_coin(symbol, expected):
    assert _parse_canonical(symbol) == expected


@pytest.mark.parametrize("symbol", [
    "us:NASDAQ:AAPL",
    "tw:TPE:2330",
    "poly:something",
    "BTC",                    # not canonical
    "",
])
def test_parse_canonical_rejects_non_crypto(symbol):
    assert _parse_canonical(symbol) is None


# ================================================================== #
# _pick_interval
# ================================================================== #
def test_pick_interval_default_when_no_fit():
    # max_drift=30s — smaller than even 1m. Falls back to default 15m.
    assert _pick_interval(30, prefer="15m") == "15m"


def test_pick_interval_picks_largest_fitting():
    assert _pick_interval(900, prefer="15m") == "15m"   # exactly 15m
    assert _pick_interval(1800, prefer="15m") == "30m"  # 30m fits
    assert _pick_interval(3600, prefer="15m") == "1h"
    assert _pick_interval(86400, prefer="15m") == "1d"


# ================================================================== #
# Fake HL Info
# ================================================================== #
class FakeHLInfo:
    """Records calls + returns canned candles."""

    def __init__(self, candles_by_coin: dict[str, list[dict]] | None = None):
        self._candles = candles_by_coin or {}
        self.calls: list[dict] = []
        self.raises: Exception | None = None

    def candles_snapshot(self, name, interval, startTime, endTime):
        self.calls.append({"name": name, "interval": interval, "startTime": startTime, "endTime": endTime})
        if self.raises:
            raise self.raises
        return list(self._candles.get(name, []))


def make_candle(start_ms: int, close_px: float, interval: str = "15m") -> dict:
    """HL candle shape — only fields the fetcher reads."""
    return {
        "t": start_ms,
        "T": start_ms + 900_000,
        "o": str(close_px),
        "h": str(close_px),
        "l": str(close_px),
        "c": str(close_px),
        "v": "100",
        "i": interval,
        "s": "BTC",
        "n": 50,
    }


# ================================================================== #
# get_close_at — happy path
# ================================================================== #
def test_close_at_exact_bar():
    ts = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    bar_ms = int(ts.timestamp() * 1000)
    info = FakeHLInfo({"BTC": [make_candle(bar_ms, 50_000.0)]})
    fetcher = HLPriceFetcher(info)

    assert fetcher.get_close_at("crypto:hyperliquid:BTC", ts) == 50_000.0


def test_close_at_picks_closest_bar():
    """Among candles, pick the one whose start_ts is closest to query ts."""
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    candles = [
        make_candle(int((base - timedelta(minutes=15)).timestamp() * 1000), 50_000.0),
        make_candle(int(base.timestamp() * 1000), 51_000.0),
        make_candle(int((base + timedelta(minutes=15)).timestamp() * 1000), 52_000.0),
    ]
    info = FakeHLInfo({"BTC": candles})
    fetcher = HLPriceFetcher(info)

    # Query at 12:05 → closest is the 12:00 bar (51_000)
    px = fetcher.get_close_at(
        "crypto:hyperliquid:BTC", base + timedelta(minutes=5),
    )
    assert px == 51_000.0


def test_unsupported_symbol_raises_unavailable():
    info = FakeHLInfo()
    fetcher = HLPriceFetcher(info)
    with pytest.raises(PriceUnavailable, match="cannot parse"):
        fetcher.get_close_at("us:NASDAQ:AAPL", datetime.now(timezone.utc))


def test_no_candles_returned_raises_unavailable():
    info = FakeHLInfo({"BTC": []})
    fetcher = HLPriceFetcher(info)
    with pytest.raises(PriceUnavailable, match="no candles"):
        fetcher.get_close_at("crypto:hyperliquid:BTC", datetime.now(timezone.utc))


def test_hl_api_exception_wrapped_as_unavailable():
    info = FakeHLInfo()
    info.raises = ConnectionError("HL down")
    fetcher = HLPriceFetcher(info)
    with pytest.raises(PriceUnavailable, match="HL candles_snapshot"):
        fetcher.get_close_at("crypto:hyperliquid:BTC", datetime.now(timezone.utc))


def test_drift_exceeded_raises_unavailable():
    """Closest bar is way off → PriceUnavailable, not stale price."""
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    far_ts_ms = int((base - timedelta(hours=10)).timestamp() * 1000)
    info = FakeHLInfo({"BTC": [make_candle(far_ts_ms, 50_000.0)]})
    fetcher = HLPriceFetcher(info)

    with pytest.raises(PriceUnavailable, match="drifted"):
        fetcher.get_close_at(
            "crypto:hyperliquid:BTC", base, max_drift_seconds=300,
        )


def test_malformed_close_raises_unavailable():
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    bar = make_candle(int(base.timestamp() * 1000), 50_000.0)
    bar["c"] = "not-a-number"
    info = FakeHLInfo({"BTC": [bar]})
    fetcher = HLPriceFetcher(info)

    with pytest.raises(PriceUnavailable, match="malformed"):
        fetcher.get_close_at("crypto:hyperliquid:BTC", base)


# ================================================================== #
# Cache
# ================================================================== #
def test_cache_avoids_duplicate_api_calls():
    """Two calls at the same minute bucket should hit cache."""
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    bar_ms = int(base.timestamp() * 1000)
    info = FakeHLInfo({"BTC": [make_candle(bar_ms, 50_000.0)]})
    fetcher = HLPriceFetcher(info)

    fetcher.get_close_at("crypto:hyperliquid:BTC", base)
    fetcher.get_close_at("crypto:hyperliquid:BTC", base + timedelta(seconds=10))

    assert len(info.calls) == 1


def test_cache_evicts_when_full():
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    info = FakeHLInfo({"BTC": [make_candle(int(base.timestamp() * 1000), 100.0)]})
    fetcher = HLPriceFetcher(info, cache_size=2)

    # Fill beyond capacity — different timestamps create different cache buckets
    for i in range(5):
        ts = base + timedelta(hours=i)
        info._candles = {"BTC": [make_candle(int(ts.timestamp() * 1000), 100.0)]}
        try:
            fetcher.get_close_at("crypto:hyperliquid:BTC", ts)
        except PriceUnavailable:
            pass  # different buckets may not have matching candles

    # Cache size capped at 2 (clears once full)
    assert len(fetcher._cache) <= 2
