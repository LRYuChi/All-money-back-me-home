"""Market regime detection — system-wide context for SupertrendStrategy (R48).

The pre-R48 strategy used per-pair indicators (1D/4H/1H trend alignment,
ADX, ATR) to gate entries. But these capture LOCAL state — they can't
tell us "is the entire crypto market in a trend regime, or in a chop
regime?". Without that context, the strategy keeps firing entries during
prolonged chop periods and gets sliced repeatedly.

This module computes 3 system-wide indicators on BTC (the proxy for
overall crypto regime) and classifies into 4 states:

  TRENDING            — clean directional regime, full sizing OK
  VOLATILE_TRENDING   — trend with high vol; reduce size slightly
  CHOPPY              — sideways / mean-reverting; reduce size heavily
  DEAD                — extreme low vol or breakdown; pause new entries

Indicators:

  1. atr_price_ratio     — BTC 30d ATR / current price
                            normal: 0.02-0.04 (2-4%/day)
                            high:   > 0.04 (volatile regime)
                            dead:   < 0.015 (eerily flat)

  2. adx_30d_median      — median ADX over last 30 daily bars
                            > 25 = trending market
                            < 20 = choppy market

  3. hurst_exponent      — fractal dimension of price series (lookback 100)
                            > 0.55 = trending (persistent)
                            ~ 0.50 = random walk
                            < 0.45 = mean-reverting

The classifier is a simple decision tree (transparent, debuggable —
no ML training data hassle):

  if adx >= 25 and hurst >= 0.55:
      return VOLATILE_TRENDING if atr > 0.04 else TRENDING
  if adx < 20 and hurst < 0.50:
      return CHOPPY
  if atr < 0.015:
      return DEAD
  return CHOPPY    # default — be conservative

Cache: 5-min TTL (30d daily candles don't change minute-by-minute).
Failure mode: if BTC fetch fails, returns UNKNOWN — caller decides
how to handle (default: treat as CHOPPY for safety).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    """4 regime states + UNKNOWN for fetch failures."""
    TRENDING = "trending"
    VOLATILE_TRENDING = "volatile_trending"
    CHOPPY = "choppy"
    DEAD = "dead"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class RegimeSnapshot:
    """One regime-detection result. Self-describing — caller can log
    or render without re-deriving."""
    regime: Regime
    atr_price_ratio: float       # BTC 30d ATR / current price
    adx_30d_median: float        # median ADX over last 30 daily bars
    hurst_exponent: float        # fractal dimension (lookback 100)
    btc_price: float             # for context
    sample_size_days: int        # daily bars used (for confidence)
    ts: datetime                 # when this snapshot was computed

    def as_compact_str(self) -> str:
        """One-line representation suitable for logs / Telegram."""
        return (
            f"{self.regime.value} "
            f"(ATR={self.atr_price_ratio:.2%}, "
            f"ADX={self.adx_30d_median:.1f}, "
            f"H={self.hurst_exponent:.2f}, "
            f"BTC=${self.btc_price:.0f})"
        )


@dataclass(slots=True, frozen=True)
class SizingAdjustment:
    """How a regime should adjust position sizing + cooldown."""
    kelly_multiplier: float      # multiply target_pct by this
    cooldown_hours: float        # circuit breaker cooldown
    block_new_entries: bool      # if True, return 0 stake immediately
    reason: str

    @classmethod
    def for_regime(cls, regime: Regime) -> "SizingAdjustment":
        """Lookup table — single source of truth for regime → sizing rules.

        Tuning notes:
          - TRENDING is reference (1.0× / 6h cooldown)
          - VOLATILE_TRENDING: still trade but smaller (vol = wider stops needed)
          - CHOPPY: shrink hard (most losses come from here)
          - DEAD: full stop — no edge in flat markets
          - UNKNOWN: fail-safe = treat as CHOPPY
        """
        table = {
            Regime.TRENDING:           cls(1.00,  6.0, False,
                                           "trending — full sizing"),
            Regime.VOLATILE_TRENDING:  cls(0.70, 12.0, False,
                                           "volatile trending — reduced sizing"),
            Regime.CHOPPY:             cls(0.30, 48.0, False,
                                           "choppy — heavy reduction (mean-reverting risk)"),
            Regime.DEAD:               cls(0.00, 72.0, True,
                                           "dead market — no edge, blocking entries"),
            Regime.UNKNOWN:            cls(0.30, 48.0, False,
                                           "regime unknown (fetch failed) — defaulting to choppy"),
        }
        return table[regime]


# =================================================================== #
# Indicator computations (pure numpy/pandas, no Freqtrade dep)
# =================================================================== #
def compute_atr_price_ratio(daily_ohlc: pd.DataFrame, period: int = 30) -> float:
    """ATR / Price ratio over `period` days. Returns ratio (0.025 = 2.5%)."""
    if len(daily_ohlc) < period + 1:
        return 0.0
    high = daily_ohlc["high"].values
    low = daily_ohlc["low"].values
    close = daily_ohlc["close"].values

    # True range
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    # Simple average over last `period` true ranges
    atr = float(np.mean(tr[-period:]))
    last_price = float(close[-1])
    return atr / last_price if last_price > 0 else 0.0


def compute_adx_30d_median(daily_ohlc: pd.DataFrame, period: int = 14,
                            window: int = 30) -> float:
    """Median ADX over the last `window` daily bars. Standard ADX(14)
    computation — manual implementation so we don't depend on talib here.
    """
    if len(daily_ohlc) < period + window + 1:
        return 0.0

    high = daily_ohlc["high"].values
    low = daily_ohlc["low"].values
    close = daily_ohlc["close"].values

    # +DM / -DM
    high_diff = high[1:] - high[:-1]
    low_diff = low[:-1] - low[1:]
    plus_dm = np.where(
        (high_diff > low_diff) & (high_diff > 0), high_diff, 0.0,
    )
    minus_dm = np.where(
        (low_diff > high_diff) & (low_diff > 0), low_diff, 0.0,
    )

    # True range
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )

    # Smooth via Wilder's EMA approximation (simple rolling for clarity)
    # Use pandas to handle the rolling smooth cleanly
    tr_s = pd.Series(tr).rolling(window=period, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm).rolling(window=period, min_periods=period).mean() / tr_s
    minus_di = 100 * pd.Series(minus_dm).rolling(window=period, min_periods=period).mean() / tr_s

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.rolling(window=period, min_periods=period).mean()

    # Median over last `window` valid values
    valid = adx.dropna()
    if len(valid) < window:
        return float(valid.median()) if len(valid) > 0 else 0.0
    return float(valid.iloc[-window:].median())


def compute_hurst_exponent(daily_ohlc: pd.DataFrame, lookback: int = 100) -> float:
    """Hurst exponent via R/S analysis. Returns value in [0, 1]:
      > 0.55 = persistent (trending)
      ~ 0.50 = random walk
      < 0.45 = anti-persistent (mean-reverting)

    Uses log returns so absolute price level doesn't matter.
    Lookback default 100 days — shorter than 200 to react faster to
    regime shifts.
    """
    if len(daily_ohlc) < lookback + 1:
        return 0.5    # default to random walk when insufficient data

    closes = daily_ohlc["close"].values[-lookback - 1:]
    log_returns = np.diff(np.log(closes))

    # Standard R/S over multiple subseries lengths
    n = len(log_returns)
    if n < 20:
        return 0.5

    # Try a few lag lengths; fit log(R/S) ~ H × log(lag)
    lags = [10, 20, 30, 50, 70, 100]
    lags = [lag for lag in lags if lag <= n // 2]
    if len(lags) < 2:
        return 0.5

    rs_values = []
    valid_lags = []
    for lag in lags:
        # split into chunks of size `lag`, compute R/S per chunk, average
        n_chunks = n // lag
        if n_chunks < 1:
            continue
        chunk_rs: list[float] = []
        for i in range(n_chunks):
            chunk = log_returns[i * lag: (i + 1) * lag]
            mean = chunk.mean()
            deviations = chunk - mean
            cumsum = np.cumsum(deviations)
            r = cumsum.max() - cumsum.min()
            s = chunk.std(ddof=0)
            if s > 0:
                chunk_rs.append(r / s)
        if chunk_rs:
            rs_values.append(np.mean(chunk_rs))
            valid_lags.append(lag)

    if len(rs_values) < 2:
        return 0.5

    # Fit slope of log(R/S) vs log(lag) via least squares
    log_lags = np.log(valid_lags)
    log_rs = np.log(rs_values)
    # slope = cov / var
    h = float(np.polyfit(log_lags, log_rs, 1)[0])
    # Clip to sensible range (numerical noise can push outside [0, 1])
    return max(0.0, min(1.0, h))


# =================================================================== #
# Classifier
# =================================================================== #
def classify_regime(
    atr_price_ratio: float,
    adx_30d_median: float,
    hurst_exponent: float,
    *,
    atr_high_threshold: float = 0.04,
    atr_dead_threshold: float = 0.015,
    adx_trend_threshold: float = 25.0,
    adx_chop_threshold: float = 20.0,
    hurst_trend_threshold: float = 0.55,
    hurst_mr_threshold: float = 0.50,
) -> Regime:
    """Decision-tree classifier. Inputs MAY be 0 if computation failed —
    we treat zero ADX/Hurst as "low confidence" → default CHOPPY.

    The thresholds match `compute_*` defaults in the docstring at top.
    Caller can override via kwargs for testing or alternative tuning.

    Order matters: trending check FIRST (a clean low-vol uptrend has
    low ATR but high ADX — it's not dead, it's "smoothly trending").
    DEAD requires BOTH low ATR AND low trend strength.
    """
    # Trending regime FIRST: high ADX + persistent Hurst overrides
    # the low-ATR check (smooth uptrends can have small daily ranges).
    # Also: if ADX is overwhelmingly strong (>40), trust it even if
    # Hurst is noisy (Hurst can be unstable on near-deterministic series).
    strong_adx = adx_30d_median >= 40.0
    if (
        adx_30d_median >= adx_trend_threshold
        and (hurst_exponent >= hurst_trend_threshold or strong_adx)
    ):
        if atr_price_ratio >= atr_high_threshold:
            return Regime.VOLATILE_TRENDING
        return Regime.TRENDING

    # Dead market: low ATR + no trend strength (both must hold)
    if (
        0 < atr_price_ratio < atr_dead_threshold
        and adx_30d_median < adx_trend_threshold
    ):
        return Regime.DEAD

    # Choppy regime: low ADX + mean-reverting Hurst
    if adx_30d_median < adx_chop_threshold and hurst_exponent < hurst_mr_threshold:
        return Regime.CHOPPY

    # Default: be conservative — treat ambiguous as CHOPPY
    return Regime.CHOPPY


# =================================================================== #
# Cached detector
# =================================================================== #
class MarketRegimeDetector:
    """Wraps a daily-OHLC fetcher with TTL cache + regime detection.

    Caller injects `fetch_btc_daily()` so tests can supply synthetic data
    and prod can use Freqtrade's dataframe API.
    """

    def __init__(
        self,
        fetch_btc_daily: Callable[[], pd.DataFrame],
        *,
        ttl_seconds: float = 300.0,
    ):
        self._fetch = fetch_btc_daily
        self._ttl = ttl_seconds
        self._cached: Optional[RegimeSnapshot] = None

    def detect(self, *, force_refresh: bool = False) -> RegimeSnapshot:
        """Return current snapshot. Re-computes if cache stale or forced."""
        now = datetime.now(timezone.utc)
        if (
            not force_refresh
            and self._cached is not None
            and (now - self._cached.ts).total_seconds() < self._ttl
        ):
            return self._cached

        try:
            df = self._fetch()
        except Exception as e:
            logger.warning("MarketRegimeDetector: BTC fetch failed (%s)", e)
            return RegimeSnapshot(
                regime=Regime.UNKNOWN,
                atr_price_ratio=0.0, adx_30d_median=0.0, hurst_exponent=0.5,
                btc_price=0.0, sample_size_days=0, ts=now,
            )

        if df is None or len(df) < 50:
            logger.warning(
                "MarketRegimeDetector: insufficient BTC data (%d rows)",
                0 if df is None else len(df),
            )
            return RegimeSnapshot(
                regime=Regime.UNKNOWN,
                atr_price_ratio=0.0, adx_30d_median=0.0, hurst_exponent=0.5,
                btc_price=0.0, sample_size_days=0 if df is None else len(df),
                ts=now,
            )

        atr = compute_atr_price_ratio(df)
        adx = compute_adx_30d_median(df)
        hurst = compute_hurst_exponent(df)
        regime = classify_regime(atr, adx, hurst)

        snap = RegimeSnapshot(
            regime=regime,
            atr_price_ratio=atr,
            adx_30d_median=adx,
            hurst_exponent=hurst,
            btc_price=float(df["close"].iloc[-1]),
            sample_size_days=len(df),
            ts=now,
        )
        self._cached = snap
        return snap

    def reset(self) -> None:
        """Bust cache — caller forces re-detection on next .detect()."""
        self._cached = None


# =================================================================== #
# NoOp detector — used when SUPERTREND_REGIME_FILTER=0 (escape hatch)
# =================================================================== #
class NoOpRegimeDetector:
    """Returns TRENDING always — effectively disables regime gating.

    Used when SUPERTREND_REGIME_FILTER=0 env is set, so ops can
    immediately revert to pre-R48 behavior without restart drama.
    """

    def detect(self, *, force_refresh: bool = False) -> RegimeSnapshot:
        return RegimeSnapshot(
            regime=Regime.TRENDING,
            atr_price_ratio=0.03, adx_30d_median=30.0, hurst_exponent=0.6,
            btc_price=0.0, sample_size_days=0,
            ts=datetime.now(timezone.utc),
        )

    def reset(self) -> None:
        pass


__all__ = [
    "Regime",
    "RegimeSnapshot",
    "SizingAdjustment",
    "MarketRegimeDetector",
    "NoOpRegimeDetector",
    "compute_atr_price_ratio",
    "compute_adx_30d_median",
    "compute_hurst_exponent",
    "classify_regime",
]
