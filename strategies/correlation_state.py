"""Cross-pair correlation + BTC dominance rotation — R53.

Two related but distinct signals:

1. Correlation matrix (rolling 30d) on log returns.
   When portfolio open positions are highly correlated (mean ρ > 0.85),
   the strategy is taking concentrated risk under the guise of
   diversification. Caller can deny new entries that would push the
   matrix above threshold.

2. BTC dominance phase classifier:
     - "btc_strong"   = BTC outperforming alts (alt season FAR away)
     - "consolidation" = BTC sideways, no clear winner
     - "alt_season"   = alts outperforming BTC

   In alt_season, the strategy can scale alt positions UP and BTC/ETH
   positions DOWN — capturing rotation flow.

Both functions are pure: caller injects OHLCV histories. Tests use
synthetic fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

import numpy as np
import pandas as pd


class DominancePhase(str, Enum):
    BTC_STRONG = "btc_strong"
    CONSOLIDATION = "consolidation"
    ALT_SEASON = "alt_season"
    UNKNOWN = "unknown"


@dataclass(slots=True, frozen=True)
class CorrelationSnapshot:
    """Per-tick state used to gate new entries."""
    matrix: pd.DataFrame              # NxN ρ over lookback
    mean_correlation: float           # mean of off-diagonal entries
    dominance_phase: DominancePhase
    btc_30d_return: float             # BTC return over lookback (decimal)
    alt_avg_30d_return: float         # mean of non-BTC returns over lookback
    pairs_seen: list[str]


# =================================================================== #
# Correlation matrix
# =================================================================== #
def rolling_correlation_matrix(
    closes_by_pair: dict[str, Sequence[float]],
    *,
    lookback_days: int = 30,
) -> pd.DataFrame:
    """Compute pairwise correlation of log returns over `lookback_days`.

    Returns NxN DataFrame with pairs as both index and columns. NaNs
    appear if any pair has insufficient data. Diagonal is always 1.0.

    Pairs with < lookback_days+1 closes are excluded entirely.
    """
    valid = {
        p: list(c)[-(lookback_days + 1):]
        for p, c in closes_by_pair.items()
        if len(c) >= lookback_days + 1
    }
    if len(valid) < 2:
        return pd.DataFrame()

    log_returns = {}
    for p, closes in valid.items():
        arr = np.array(closes, dtype=float)
        log_returns[p] = np.diff(np.log(arr))

    df = pd.DataFrame(log_returns)
    return df.corr()


def mean_off_diagonal(matrix: pd.DataFrame) -> float:
    """Mean of all non-diagonal correlation values."""
    if matrix.empty or len(matrix) < 2:
        return 0.0
    n = len(matrix)
    # Sum all - diagonal sum, divide by # off-diagonal cells
    total = matrix.values.sum()
    diag = np.trace(matrix.values)
    off_count = n * n - n
    if off_count == 0:
        return 0.0
    return float((total - diag) / off_count)


def is_concentrated(matrix: pd.DataFrame, threshold: float = 0.85) -> bool:
    """Is mean off-diagonal correlation ≥ threshold?
    True = portfolio is taking concentrated risk despite multiple pairs."""
    return mean_off_diagonal(matrix) >= threshold


# =================================================================== #
# BTC dominance phase
# =================================================================== #
def classify_dominance_phase(
    closes_by_pair: dict[str, Sequence[float]],
    *,
    lookback_days: int = 30,
    btc_strong_threshold: float = 0.05,
    alt_season_threshold: float = 0.05,
) -> tuple[DominancePhase, float, float]:
    """Compare BTC return vs avg-alt return over lookback.

    BTC outperforms alts by btc_strong_threshold → BTC_STRONG
    Alts outperform BTC by alt_season_threshold → ALT_SEASON
    Otherwise → CONSOLIDATION

    Returns (phase, btc_return, alt_avg_return).
    """
    btc_pair = _find_btc_pair(closes_by_pair.keys())
    if btc_pair is None:
        return DominancePhase.UNKNOWN, 0.0, 0.0

    btc_closes = list(closes_by_pair[btc_pair])
    if len(btc_closes) < lookback_days + 1:
        return DominancePhase.UNKNOWN, 0.0, 0.0

    btc_return = (btc_closes[-1] / btc_closes[-(lookback_days + 1)]) - 1

    alt_returns = []
    for pair, closes in closes_by_pair.items():
        if pair == btc_pair:
            continue
        c = list(closes)
        if len(c) < lookback_days + 1:
            continue
        alt_returns.append((c[-1] / c[-(lookback_days + 1)]) - 1)

    if not alt_returns:
        return DominancePhase.UNKNOWN, btc_return, 0.0

    alt_avg = float(np.mean(alt_returns))
    diff = btc_return - alt_avg

    if diff >= btc_strong_threshold:
        return DominancePhase.BTC_STRONG, btc_return, alt_avg
    if diff <= -alt_season_threshold:
        return DominancePhase.ALT_SEASON, btc_return, alt_avg
    return DominancePhase.CONSOLIDATION, btc_return, alt_avg


def _find_btc_pair(pair_keys) -> str | None:
    """Tolerant: matches BTC/USDT:USDT, BTC/USDT, BTCUSDT, etc."""
    for p in pair_keys:
        if "BTC" in p.upper() and ("USDT" in p.upper() or "USD" in p.upper()):
            return p
    return None


# =================================================================== #
# Sizing adjustment for rotation phase
# =================================================================== #
def rotation_sizing_multiplier(
    phase: DominancePhase, pair: str,
) -> float:
    """How to scale a new entry's size based on rotation phase.

    BTC_STRONG: BTC/ETH at 1.0×, alts at 0.7× (avoid late alts)
    ALT_SEASON: BTC/ETH at 0.7×, alts at 1.2× (ride rotation)
    CONSOLIDATION/UNKNOWN: 1.0× across the board
    """
    is_btc_or_eth = (
        "BTC" in pair.upper() or "ETH" in pair.upper()
    )
    if phase == DominancePhase.BTC_STRONG:
        return 1.0 if is_btc_or_eth else 0.7
    if phase == DominancePhase.ALT_SEASON:
        return 0.7 if is_btc_or_eth else 1.2
    return 1.0


# =================================================================== #
# Snapshot composer
# =================================================================== #
def build_snapshot(
    closes_by_pair: dict[str, Sequence[float]],
    *,
    lookback_days: int = 30,
) -> CorrelationSnapshot:
    """Single-call wrapper used by the live strategy."""
    matrix = rolling_correlation_matrix(
        closes_by_pair, lookback_days=lookback_days,
    )
    mean_corr = mean_off_diagonal(matrix)
    phase, btc_ret, alt_ret = classify_dominance_phase(
        closes_by_pair, lookback_days=lookback_days,
    )
    return CorrelationSnapshot(
        matrix=matrix,
        mean_correlation=mean_corr,
        dominance_phase=phase,
        btc_30d_return=btc_ret,
        alt_avg_30d_return=alt_ret,
        pairs_seen=list(matrix.columns) if not matrix.empty else [],
    )


__all__ = [
    "DominancePhase",
    "CorrelationSnapshot",
    "rolling_correlation_matrix",
    "mean_off_diagonal",
    "is_concentrated",
    "classify_dominance_phase",
    "rotation_sizing_multiplier",
    "build_snapshot",
]
