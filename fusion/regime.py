"""Pure-rules market regime classifier.

Inputs (MarketContext):
  - btc_price        — current BTC spot
  - btc_ma200        — 200-day moving average
  - btc_ma200_slope  — daily Δ MA200 / MA200 (e.g. +0.001 = +0.1%/day)
  - btc_realized_vol — annualized 60d realized vol (BTC); typical 0.4-1.2
  - vix              — CBOE VIX (US stocks fear); 12-15 calm, 30+ stress
  - daily_dd_pct     — current intraday drawdown vs prev close (positive = drop)

Output (Regime enum):
  CRISIS / BULL_TRENDING / BULL_CHOPPY /
  BEAR_TRENDING / BEAR_CHOPPY /
  SIDEWAYS_LOW_VOL / SIDEWAYS_HIGH_VOL

Decision rules (in priority order — CRISIS overrides everything):

  1. CRISIS              VIX > 35  OR  daily_dd_pct > 0.05 (5%)

  2. Above 200MA + rising → bull
       BULL_TRENDING       vol < 0.6
       BULL_CHOPPY         vol >= 0.6

  3. Below 200MA + falling → bear
       BEAR_TRENDING       vol < 0.6
       BEAR_CHOPPY         vol >= 0.6

  4. Otherwise (price near 200MA OR slope ≈ flat) → sideways
       SIDEWAYS_LOW_VOL    vol < 0.4
       SIDEWAYS_HIGH_VOL   vol >= 0.4

Thresholds are conservative defaults — tune via constructor args once
the reflection loop has proven which thresholds discriminate best.

Missing data: any required field None → returns SIDEWAYS_HIGH_VOL as
the "I don't know, assume worst-of-safe" fallback. Strategy authors
should not write entries that depend on regime when data is unavailable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class Regime(str, Enum):
    """Closed taxonomy. Adding a new value requires updating fusion weights
    and any strategy DSL `none_of: regime == ...` predicates."""

    CRISIS = "CRISIS"
    BULL_TRENDING = "BULL_TRENDING"
    BULL_CHOPPY = "BULL_CHOPPY"
    BEAR_TRENDING = "BEAR_TRENDING"
    BEAR_CHOPPY = "BEAR_CHOPPY"
    SIDEWAYS_LOW_VOL = "SIDEWAYS_LOW_VOL"
    SIDEWAYS_HIGH_VOL = "SIDEWAYS_HIGH_VOL"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True, frozen=True)
class MarketContext:
    """Inputs for regime classification. Any field can be None when data
    is unavailable — detector handles gracefully (returns UNKNOWN or
    falls back to SIDEWAYS_HIGH_VOL per rule docs)."""

    btc_price: float | None = None
    btc_ma200: float | None = None
    btc_ma200_slope: float | None = None        # daily fractional slope
    btc_realized_vol: float | None = None       # annualized 60d
    vix: float | None = None
    daily_dd_pct: float | None = None           # positive = drop


@dataclass(slots=True, frozen=True)
class RegimeDetector:
    """Configurable thresholds — defaults are sensible starting points."""

    # CRISIS
    crisis_vix: float = 35.0
    crisis_daily_dd: float = 0.05               # 5%

    # Trend confirmation
    flat_slope_band: float = 0.0005             # |slope| < 0.05%/day = flat
    near_ma_band: float = 0.02                  # |price/MA - 1| < 2% = sideways

    # Vol cutoffs
    trend_vol_cutoff: float = 0.6               # high-vol trending → CHOPPY
    sideways_vol_cutoff: float = 0.4

    def detect(self, ctx: MarketContext) -> Regime:
        # 1. CRISIS overrides everything
        if ctx.vix is not None and ctx.vix > self.crisis_vix:
            return Regime.CRISIS
        if ctx.daily_dd_pct is not None and ctx.daily_dd_pct > self.crisis_daily_dd:
            return Regime.CRISIS

        # 2. Need price + MA + slope to reason about trend
        if ctx.btc_price is None or ctx.btc_ma200 is None or ctx.btc_ma200_slope is None:
            logger.debug("regime: missing price/MA/slope → UNKNOWN")
            return Regime.UNKNOWN

        # 3. Sideways check first — band around MA OR slope near zero
        ma_distance = abs(ctx.btc_price / ctx.btc_ma200 - 1) if ctx.btc_ma200 else 0
        near_ma = ma_distance < self.near_ma_band
        flat_slope = abs(ctx.btc_ma200_slope) < self.flat_slope_band

        if near_ma or flat_slope:
            return self._classify_sideways(ctx)

        # 4. Bull / bear by direction
        if ctx.btc_price > ctx.btc_ma200 and ctx.btc_ma200_slope > 0:
            return self._classify_bull(ctx)
        if ctx.btc_price < ctx.btc_ma200 and ctx.btc_ma200_slope < 0:
            return self._classify_bear(ctx)

        # Mixed signals (price above MA but slope falling, etc.) → choppy bias
        if ctx.btc_price > ctx.btc_ma200:
            return Regime.BULL_CHOPPY
        return Regime.BEAR_CHOPPY

    # ---------------------------------------------------------------- #
    def _classify_bull(self, ctx: MarketContext) -> Regime:
        if ctx.btc_realized_vol is None:
            return Regime.BULL_CHOPPY  # safe default
        return Regime.BULL_TRENDING if ctx.btc_realized_vol < self.trend_vol_cutoff else Regime.BULL_CHOPPY

    def _classify_bear(self, ctx: MarketContext) -> Regime:
        if ctx.btc_realized_vol is None:
            return Regime.BEAR_CHOPPY
        return Regime.BEAR_TRENDING if ctx.btc_realized_vol < self.trend_vol_cutoff else Regime.BEAR_CHOPPY

    def _classify_sideways(self, ctx: MarketContext) -> Regime:
        if ctx.btc_realized_vol is None:
            return Regime.SIDEWAYS_HIGH_VOL  # conservative
        return (
            Regime.SIDEWAYS_LOW_VOL
            if ctx.btc_realized_vol < self.sideways_vol_cutoff
            else Regime.SIDEWAYS_HIGH_VOL
        )


# Module-level convenience for callers who don't need to keep the
# detector around. Uses default thresholds.
_default_detector = RegimeDetector()


def detect_regime(ctx: MarketContext) -> Regime:
    return _default_detector.detect(ctx)


__all__ = ["Regime", "MarketContext", "RegimeDetector", "detect_regime"]
