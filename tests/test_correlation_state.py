"""Tests for strategies.correlation_state — R53 cross-pair signals."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.correlation_state import (
    CorrelationSnapshot,
    DominancePhase,
    build_snapshot,
    classify_dominance_phase,
    is_concentrated,
    mean_off_diagonal,
    rolling_correlation_matrix,
    rotation_sizing_multiplier,
)


# =================================================================== #
# Synthetic price series
# =================================================================== #
def _trending_series(start: float = 50_000, days: int = 60,
                     daily_return: float = 0.005,
                     vol: float = 0.02, seed: int = 0) -> list[float]:
    rng = np.random.default_rng(seed)
    log_ret = rng.normal(daily_return, vol, days)
    return list(start * np.exp(np.cumsum(log_ret)))


def _correlated_series(reference: list[float], correlation: float,
                       seed: int = 0) -> list[float]:
    """Generate series with target correlation to reference (rough)."""
    rng = np.random.default_rng(seed)
    ref = np.array(reference)
    log_ref = np.diff(np.log(ref))
    noise = rng.normal(0, np.std(log_ref), len(log_ref))
    new_log = correlation * log_ref + np.sqrt(1 - correlation**2) * noise
    closes = [ref[0]]
    for r in new_log:
        closes.append(closes[-1] * np.exp(r))
    return closes


# =================================================================== #
# rolling_correlation_matrix
# =================================================================== #
def test_correlation_matrix_diagonal_is_one():
    closes = {
        "BTC": _trending_series(seed=1),
        "ETH": _trending_series(seed=2),
    }
    m = rolling_correlation_matrix(closes, lookback_days=30)
    assert np.allclose(np.diag(m.values), 1.0)


def test_correlation_matrix_symmetric():
    closes = {
        "BTC": _trending_series(seed=1),
        "ETH": _trending_series(seed=2),
        "SOL": _trending_series(seed=3),
    }
    m = rolling_correlation_matrix(closes, lookback_days=30)
    assert np.allclose(m.values, m.values.T)


def test_correlation_matrix_high_for_correlated_series():
    """Series generated with target ρ=0.9 should compute close to 0.9."""
    btc = _trending_series(seed=42)
    eth = _correlated_series(btc, correlation=0.9, seed=99)
    closes = {"BTC": btc, "ETH": eth}
    m = rolling_correlation_matrix(closes, lookback_days=50)
    assert m.loc["BTC", "ETH"] > 0.7   # close to 0.9, allow slack


def test_correlation_matrix_excludes_short_history():
    """Pairs with < lookback+1 closes are dropped. With only 1 valid
    pair remaining, the matrix is empty (correlation needs ≥ 2 pairs)."""
    closes = {
        "BTC": _trending_series(days=60, seed=1),
        "ETH": _trending_series(days=60, seed=2),
        "TOO_SHORT": _trending_series(days=10, seed=3),
    }
    m = rolling_correlation_matrix(closes, lookback_days=30)
    assert "BTC" in m.columns
    assert "ETH" in m.columns
    assert "TOO_SHORT" not in m.columns


def test_correlation_matrix_empty_when_too_few_pairs():
    closes = {"BTC": _trending_series(seed=1)}
    m = rolling_correlation_matrix(closes)
    assert m.empty


def test_correlation_matrix_handles_no_pairs():
    assert rolling_correlation_matrix({}).empty


# =================================================================== #
# mean_off_diagonal
# =================================================================== #
def test_mean_off_diag_balanced():
    """3-pair matrix, all off-diagonal = 0.5 → mean 0.5."""
    m = pd.DataFrame(
        [[1.0, 0.5, 0.5], [0.5, 1.0, 0.5], [0.5, 0.5, 1.0]],
        index=["A", "B", "C"], columns=["A", "B", "C"],
    )
    assert mean_off_diagonal(m) == pytest.approx(0.5)


def test_mean_off_diag_zero_for_identity():
    m = pd.DataFrame(
        [[1.0, 0.0], [0.0, 1.0]],
        index=["A", "B"], columns=["A", "B"],
    )
    assert mean_off_diagonal(m) == 0.0


def test_mean_off_diag_empty_returns_zero():
    assert mean_off_diagonal(pd.DataFrame()) == 0.0


def test_mean_off_diag_single_element_returns_zero():
    m = pd.DataFrame([[1.0]], index=["A"], columns=["A"])
    assert mean_off_diagonal(m) == 0.0


# =================================================================== #
# is_concentrated
# =================================================================== #
def test_concentrated_when_above_threshold():
    m = pd.DataFrame(
        [[1.0, 0.9], [0.9, 1.0]],
        index=["A", "B"], columns=["A", "B"],
    )
    assert is_concentrated(m, threshold=0.85) is True


def test_not_concentrated_when_below_threshold():
    m = pd.DataFrame(
        [[1.0, 0.5], [0.5, 1.0]],
        index=["A", "B"], columns=["A", "B"],
    )
    assert is_concentrated(m, threshold=0.85) is False


def test_concentrated_at_exact_threshold():
    """Boundary: ρ == threshold → True (≥ check)."""
    m = pd.DataFrame(
        [[1.0, 0.85], [0.85, 1.0]],
        index=["A", "B"], columns=["A", "B"],
    )
    assert is_concentrated(m, threshold=0.85) is True


# =================================================================== #
# classify_dominance_phase
# =================================================================== #
def test_phase_btc_strong_when_btc_outperforms():
    """BTC +20%, alts -5% → BTC_STRONG."""
    btc = _trending_series(start=50_000, days=60,
                           daily_return=0.004, vol=0.005, seed=1)
    eth_down = _trending_series(start=3_000, days=60,
                                 daily_return=-0.002, vol=0.005, seed=2)
    closes = {"BTC/USDT": btc, "ETH/USDT": eth_down}
    phase, btc_r, alt_r = classify_dominance_phase(closes)
    assert phase == DominancePhase.BTC_STRONG
    assert btc_r > 0
    assert alt_r < btc_r


def test_phase_alt_season_when_alts_outperform():
    btc = _trending_series(start=50_000, days=60,
                           daily_return=-0.001, vol=0.005, seed=1)
    eth_up = _trending_series(start=3_000, days=60,
                              daily_return=0.005, vol=0.005, seed=2)
    sol_up = _trending_series(start=100, days=60,
                              daily_return=0.005, vol=0.005, seed=3)
    closes = {"BTC/USDT": btc, "ETH/USDT": eth_up, "SOL/USDT": sol_up}
    phase, _, _ = classify_dominance_phase(closes)
    assert phase == DominancePhase.ALT_SEASON


def test_phase_consolidation_when_close_returns():
    btc = _trending_series(start=50_000, days=60,
                           daily_return=0.001, vol=0.005, seed=1)
    eth = _trending_series(start=3_000, days=60,
                           daily_return=0.001, vol=0.005, seed=2)
    closes = {"BTC/USDT": btc, "ETH/USDT": eth}
    phase, _, _ = classify_dominance_phase(closes)
    assert phase == DominancePhase.CONSOLIDATION


def test_phase_unknown_when_no_btc_pair():
    closes = {"ETH/USDT": _trending_series(seed=1),
              "SOL/USDT": _trending_series(seed=2)}
    phase, _, _ = classify_dominance_phase(closes)
    assert phase == DominancePhase.UNKNOWN


def test_phase_unknown_when_btc_history_too_short():
    closes = {
        "BTC/USDT": _trending_series(days=20, seed=1),
        "ETH/USDT": _trending_series(days=60, seed=2),
    }
    phase, _, _ = classify_dominance_phase(closes, lookback_days=30)
    assert phase == DominancePhase.UNKNOWN


def test_phase_unknown_when_no_alt_pairs():
    """Only BTC, no alts to compare."""
    closes = {"BTC/USDT": _trending_series(seed=1)}
    phase, _, _ = classify_dominance_phase(closes)
    assert phase == DominancePhase.UNKNOWN


# =================================================================== #
# rotation_sizing_multiplier
# =================================================================== #
def test_rotation_btc_strong_alt_reduced():
    """In BTC_STRONG: alts get 0.7×, BTC/ETH stay 1.0×."""
    assert rotation_sizing_multiplier(DominancePhase.BTC_STRONG, "BTC/USDT") == 1.0
    assert rotation_sizing_multiplier(DominancePhase.BTC_STRONG, "ETH/USDT") == 1.0
    assert rotation_sizing_multiplier(DominancePhase.BTC_STRONG, "SOL/USDT") == 0.7


def test_rotation_alt_season_alt_boosted():
    """In ALT_SEASON: alts 1.2×, BTC/ETH 0.7×."""
    assert rotation_sizing_multiplier(DominancePhase.ALT_SEASON, "BTC/USDT") == 0.7
    assert rotation_sizing_multiplier(DominancePhase.ALT_SEASON, "ETH/USDT") == 0.7
    assert rotation_sizing_multiplier(DominancePhase.ALT_SEASON, "AVAX/USDT") == 1.2


def test_rotation_consolidation_neutral():
    """No rotation bias → all 1.0×."""
    assert rotation_sizing_multiplier(DominancePhase.CONSOLIDATION, "BTC/USDT") == 1.0
    assert rotation_sizing_multiplier(DominancePhase.CONSOLIDATION, "SOL/USDT") == 1.0


def test_rotation_unknown_neutral():
    assert rotation_sizing_multiplier(DominancePhase.UNKNOWN, "ANY/USDT") == 1.0


def test_rotation_case_insensitive():
    """Lowercase pair names should work too."""
    assert rotation_sizing_multiplier(DominancePhase.BTC_STRONG, "btc/usdt") == 1.0


# =================================================================== #
# build_snapshot
# =================================================================== #
def test_build_snapshot_returns_struct():
    closes = {
        "BTC/USDT": _trending_series(seed=1),
        "ETH/USDT": _trending_series(seed=2),
    }
    snap = build_snapshot(closes)
    assert isinstance(snap, CorrelationSnapshot)
    assert "BTC/USDT" in snap.pairs_seen
    assert -1 <= snap.mean_correlation <= 1


def test_build_snapshot_handles_empty():
    snap = build_snapshot({})
    assert snap.matrix.empty
    assert snap.mean_correlation == 0.0
    assert snap.dominance_phase == DominancePhase.UNKNOWN
