"""HLPriceFetcher — query Hyperliquid candle snapshots for forward-return calc.

Why HL: Smart Money signals come from HL whale fills, so the ground-truth
price for "what would a follow-trade have done" is HL's own kline. OKX
mid would be slightly different (basis/spread); HL self-attribution is
the cleanest comparison for SM signal accuracy.

Interval selection:
  We pick the interval whose period is ≤ max_drift_seconds, so each
  candle's start_ts is within the validator's drift budget. For the
  default 1h drift this means 15m or 1m intervals work; we default to
  15m as a tradeoff (smaller → more API rows to scan).

Rate limiting:
  HL allows ~1000 req/min. The validator runs hourly with limit=200,
  so worst case ~200 candle calls per run = trivial. A simple per-bucket
  cache keyed by (coin, interval, hour) prevents duplicate calls inside
  the same batch.

Symbol mapping:
  canonical "crypto:hyperliquid:BTC" → coin "BTC"
  canonical "crypto:OKX:BTC/USDT:USDT" → BTC (best-effort fallback)
  anything else → PriceUnavailable
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from reflection.price import PriceUnavailable

logger = logging.getLogger(__name__)


# HL supports these intervals (per docs). Keep in ascending duration order.
_HL_INTERVALS: list[tuple[str, int]] = [
    ("1m", 60),
    ("3m", 180),
    ("5m", 300),
    ("15m", 900),
    ("30m", 1800),
    ("1h", 3600),
    ("2h", 7200),
    ("4h", 14400),
    ("8h", 28800),
    ("12h", 43200),
    ("1d", 86400),
]


_CANONICAL_RE = re.compile(r"^crypto:[a-zA-Z0-9_]+:([A-Za-z0-9]+)")


class HLInfoLike(Protocol):
    """Subset of hyperliquid.info.Info we need. Tests inject a fake."""

    def candles_snapshot(
        self, name: str, interval: str, startTime: int, endTime: int
    ) -> list[dict[str, Any]]: ...


def _parse_canonical(symbol: str) -> str | None:
    """Extract HL coin name from canonical symbol.

    Accepts:
      crypto:hyperliquid:BTC  → 'BTC'
      crypto:OKX:BTC/USDT:USDT → 'BTC' (fallback when shadow used OKX)
    Rejects: us:..., tw:..., poly:...
    """
    m = _CANONICAL_RE.match(symbol)
    return m.group(1).upper() if m else None


def _pick_interval(max_drift_seconds: int, prefer: str = "15m") -> str:
    """Choose the largest interval whose period ≤ drift, defaulting to `prefer`
    when nothing fits.

    Logic: bigger interval = fewer rows to fetch + same drift coverage.
    """
    fitting = [name for (name, sec) in _HL_INTERVALS if sec <= max_drift_seconds]
    if not fitting:
        return prefer
    # Largest of the fitting intervals
    return fitting[-1]


class HLPriceFetcher:
    """PriceFetcher backed by HL candle snapshots."""

    def __init__(
        self,
        hl_info: HLInfoLike,
        *,
        default_interval: str = "15m",
        cache_size: int = 5_000,
    ) -> None:
        self._hl = hl_info
        self._default_interval = default_interval
        # Cache: (coin, interval, bucket_start_ms) → list of candles
        # Bucket = the queried window, so each cache hit returns the
        # full slice we already fetched.
        self._cache: dict[tuple[str, str, int], list[dict]] = {}
        self._cache_size = cache_size
        self._lock = threading.Lock()

    def get_close_at(
        self,
        symbol: str,
        ts: datetime,
        *,
        max_drift_seconds: int = 3600,
    ) -> float:
        coin = _parse_canonical(symbol)
        if coin is None:
            raise PriceUnavailable(f"HL fetcher: cannot parse symbol {symbol!r}")

        interval = _pick_interval(max_drift_seconds, self._default_interval)
        # Query a window [ts - drift, ts + drift] so closest-bar logic
        # has neighbours on both sides
        window_start = ts - timedelta(seconds=max_drift_seconds)
        window_end = ts + timedelta(seconds=max_drift_seconds)
        start_ms = int(window_start.timestamp() * 1000)
        end_ms = int(window_end.timestamp() * 1000)

        # Bucket key truncates start to interval boundary so adjacent
        # validator rows reuse the same query
        bucket_start = (start_ms // 60_000) * 60_000  # round to minute
        cache_key = (coin, interval, bucket_start)

        with self._lock:
            cached = self._cache.get(cache_key)
        if cached is None:
            try:
                cached = self._hl.candles_snapshot(coin, interval, start_ms, end_ms)
            except Exception as e:
                raise PriceUnavailable(
                    f"HL candles_snapshot {coin}/{interval} failed: {e}",
                ) from e
            with self._lock:
                # Naive eviction: clear when cache full
                if len(self._cache) >= self._cache_size:
                    self._cache.clear()
                self._cache[cache_key] = cached or []

        if not cached:
            raise PriceUnavailable(
                f"HL: no candles for {coin}/{interval} between "
                f"{window_start.isoformat()} and {window_end.isoformat()}",
            )

        target_ms = int(ts.timestamp() * 1000)
        # Find candle whose start_ts is closest to target — `t` is start ms
        best = min(cached, key=lambda c: abs(int(c["t"]) - target_ms))
        drift_ms = abs(int(best["t"]) - target_ms)
        if drift_ms > max_drift_seconds * 1000:
            raise PriceUnavailable(
                f"HL: closest candle for {coin} drifted {drift_ms / 1000:.0f}s "
                f"from {ts.isoformat()} (>{max_drift_seconds}s)",
            )
        try:
            return float(best["c"])
        except (KeyError, ValueError, TypeError) as e:
            raise PriceUnavailable(f"HL: malformed candle close={best!r}: {e}") from e


def build_hl_fetcher(api_url: str | None = None) -> HLPriceFetcher:
    """Convenience factory — instantiates real HL Info client.

    Tests inject a fake directly; this helper is for production wiring
    in reflection/cli/validate.py.
    """
    from hyperliquid.info import Info
    info = Info(base_url=api_url or "https://api.hyperliquid.xyz", skip_ws=True)
    return HLPriceFetcher(info)


__all__ = [
    "HLInfoLike",
    "HLPriceFetcher",
    "build_hl_fetcher",
    "_parse_canonical",
    "_pick_interval",
]
