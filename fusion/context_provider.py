"""MarketContextProvider — sources BTC price/MA/vol + VIX + DD for regime detection.

The daemon's regime_provider callback uses one of these to feed
RegimeDetector with fresh data each tick.

Backends:
  - StaticContextProvider     — fixed context, for tests + smoke
  - CachedContextProvider     — TTL wrapper around any other (avoids
                                 hammering data sources on each strategy tick)
  - HLBTCContextProvider      — pulls BTC daily candles from Hyperliquid,
                                 computes MA200 / slope / 60d realized vol
                                 / today's DD; optionally pulls VIX from a
                                 separate provider
  - YfinanceVixProvider       — pulls ^VIX from yfinance (optional dep)

For Phase D round 3 we keep VIX as a separate provider so the daemon can
run with HL alone if yfinance isn't installed (degraded but functional —
detector falls back to non-VIX-based crisis check).
"""
from __future__ import annotations

import logging
import math
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from fusion.regime import MarketContext

logger = logging.getLogger(__name__)


class MarketContextProvider(Protocol):
    def get(self) -> MarketContext: ...


# ================================================================== #
# Static — for tests + first-deploy smoke
# ================================================================== #
class StaticContextProvider:
    """Always returns the same MarketContext. Use as a deterministic
    fallback when data sources are unavailable."""

    def __init__(self, ctx: MarketContext) -> None:
        self._ctx = ctx

    def get(self) -> MarketContext:
        return self._ctx


# ================================================================== #
# Cached wrapper — TTL guard so MA200/vol calc isn't done every tick
# ================================================================== #
class CachedContextProvider:
    """Wraps another provider; recomputes only every `ttl_seconds`.

    `ttl_seconds=300` = 5 min. Regime moves slowly (BTC trend on 200d MA);
    polling every tick is wasteful. Tests can pass a callable for `now`
    to control time.
    """

    def __init__(
        self,
        upstream: MarketContextProvider,
        *,
        ttl_seconds: int = 300,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._upstream = upstream
        self._ttl = ttl_seconds
        self._now = now
        self._cache: MarketContext | None = None
        self._cached_at: float = 0.0

    def get(self) -> MarketContext:
        t = self._now()
        if self._cache is None or (t - self._cached_at) >= self._ttl:
            self._cache = self._upstream.get()
            self._cached_at = t
        return self._cache


# ================================================================== #
# HL-based BTC provider
# ================================================================== #
class HLInfoLike(Protocol):
    """Subset of hyperliquid.info.Info we need for daily candles."""

    def candles_snapshot(
        self, name: str, interval: str, startTime: int, endTime: int
    ) -> list[dict[str, Any]]: ...


class HLBTCContextProvider:
    """Pulls BTC daily candles from HL and derives:
        - btc_price       (last close)
        - btc_ma200       (200-day SMA of closes)
        - btc_ma200_slope (last 5d Δ MA200 / MA200 — daily fractional)
        - btc_realized_vol (annualized stdev of daily returns over 60d)
        - daily_dd_pct    (1 - today_close/prev_close, positive = drop)
    plus optional `vix` from a separate provider.
    """

    REQUIRED_DAYS = 210         # 200 for MA + 10 buffer
    VOL_WINDOW = 60
    SLOPE_WINDOW = 5
    ANNUALIZATION = math.sqrt(365)

    def __init__(
        self,
        hl_info: HLInfoLike,
        *,
        coin: str = "BTC",
        vix_provider: Callable[[], float | None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._hl = hl_info
        self._coin = coin
        self._vix_provider = vix_provider
        self._now = now

    def get(self) -> MarketContext:
        candles = self._fetch_daily_candles()
        if not candles or len(candles) < self.SLOPE_WINDOW + 1:
            logger.warning(
                "HLBTCContextProvider: insufficient candles (%d) — returning empty context",
                len(candles) if candles else 0,
            )
            return MarketContext(vix=self._safe_vix())

        closes = [_safe_float(c.get("c"), default=0.0) for c in candles]
        closes = [c for c in closes if c > 0]

        # MA200 — partial window OK if < 200 days, just use what we have
        ma_window = min(200, len(closes))
        ma200 = statistics.mean(closes[-ma_window:]) if ma_window else None

        # Slope: compare current MA200 to MA200 5 days ago
        slope = None
        if len(closes) >= ma_window + self.SLOPE_WINDOW:
            prior_window = closes[-(ma_window + self.SLOPE_WINDOW): -self.SLOPE_WINDOW]
            ma_prior = statistics.mean(prior_window)
            if ma_prior > 0:
                slope = (ma200 - ma_prior) / ma_prior / self.SLOPE_WINDOW

        # Realized vol — daily log returns, annualized
        vol = None
        if len(closes) >= self.VOL_WINDOW + 1:
            window = closes[-(self.VOL_WINDOW + 1):]
            rets = [
                math.log(window[i] / window[i - 1])
                for i in range(1, len(window))
                if window[i - 1] > 0 and window[i] > 0
            ]
            if len(rets) >= 2:
                vol = statistics.stdev(rets) * self.ANNUALIZATION

        # Daily DD: today_close vs previous close
        dd = None
        if len(closes) >= 2 and closes[-2] > 0:
            dd = max(0.0, (closes[-2] - closes[-1]) / closes[-2])

        return MarketContext(
            btc_price=closes[-1],
            btc_ma200=ma200,
            btc_ma200_slope=slope,
            btc_realized_vol=vol,
            daily_dd_pct=dd,
            vix=self._safe_vix(),
        )

    # ---------------------------------------------------------------- #
    def _fetch_daily_candles(self) -> list[dict]:
        end = self._now()
        start = end - timedelta(days=self.REQUIRED_DAYS)
        try:
            return self._hl.candles_snapshot(
                self._coin, "1d",
                int(start.timestamp() * 1000),
                int(end.timestamp() * 1000),
            ) or []
        except Exception as e:
            logger.warning("HLBTCContextProvider: candles_snapshot failed: %s", e)
            return []

    def _safe_vix(self) -> float | None:
        if self._vix_provider is None:
            return None
        try:
            return self._vix_provider()
        except Exception as e:
            logger.warning("VIX provider raised: %s — returning None", e)
            return None


# ================================================================== #
# yfinance VIX (optional)
# ================================================================== #
def yfinance_vix_provider() -> float | None:
    """Pull current ^VIX close via yfinance. Returns None on failure
    (yfinance not installed / network issue / weekend with no fresh data).

    Importing yfinance is deferred so this module is importable even
    without it (StaticContextProvider tests don't need it)."""
    try:
        import yfinance as yf  # type: ignore[import-untyped]
    except ImportError:
        logger.debug("yfinance not installed — VIX unavailable")
        return None

    try:
        ticker = yf.Ticker("^VIX")
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception as e:
        logger.warning("yfinance ^VIX fetch failed: %s", e)
        return None


# ================================================================== #
# Convenience builder for daemon
# ================================================================== #
def build_default_provider(
    *,
    enable_vix: bool = True,
    cache_ttl_sec: int = 300,
) -> MarketContextProvider:
    """Production wiring: HL + optional VIX, wrapped in 5min cache."""
    from hyperliquid.info import Info
    info = Info(base_url="https://api.hyperliquid.xyz", skip_ws=True)
    vix_fn = yfinance_vix_provider if enable_vix else None
    upstream = HLBTCContextProvider(info, vix_provider=vix_fn)
    return CachedContextProvider(upstream, ttl_seconds=cache_ttl_sec)


__all__ = [
    "MarketContextProvider",
    "StaticContextProvider",
    "CachedContextProvider",
    "HLBTCContextProvider",
    "HLInfoLike",
    "yfinance_vix_provider",
    "build_default_provider",
]


# ================================================================== #
# Helpers
# ================================================================== #
def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
