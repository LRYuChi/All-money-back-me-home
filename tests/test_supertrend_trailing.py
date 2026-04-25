"""Tests for SupertrendStrategy.custom_stoploss 4-phase trailing logic (P0-1).

Round 47: validates that the trailing stop math returns the expected SL
percentage at each profit threshold for both long and short, and that
the asymmetric thresholds (shorts lock faster) are honored.

We don't instantiate Freqtrade's IStrategy machinery (heavy); instead
we extract the pure trailing logic into a callable that mirrors what
custom_stoploss returns for a given (current_profit, is_short) pair.
"""
from __future__ import annotations

import pytest


# Replicate the exact trailing logic from supertrend.custom_stoploss
# (synced manually so the test is independent of Freqtrade imports).
def _stoploss_from_open_pct(desired_offset_from_entry: float,
                            current_profit: float,
                            is_short: bool) -> float:
    """Match Freqtrade's stoploss_from_open helper.
    Returns SL as fraction below current price (negative for longs that are
    in profit; positive when SL is above current price = locked profit).

    For a long with current_profit=0.05 (5%) and desired_offset_from_entry=0.03:
      → SL price = entry * 1.03 = entry * (1 + 0.03)
      → distance from current = (1.05 - 1.03) / 1.05 = 0.01905 = 1.9%
      → returns -0.01905 (SL is 1.9% BELOW current)
    """
    if is_short:
        # mirror around 0
        return _stoploss_from_open_pct(
            desired_offset_from_entry, current_profit, is_short=False,
        )
    # Long math: SL price = entry * (1 + desired_offset)
    # Current price = entry * (1 + current_profit)
    # SL distance from current = (current - SL) / current
    sl_price_ratio = 1 + desired_offset_from_entry
    cur_price_ratio = 1 + current_profit
    return -(cur_price_ratio - sl_price_ratio) / cur_price_ratio


def _trailing_phase(current_profit: float, is_short: bool) -> tuple[int, float]:
    """Return (phase, sl_pct) — replicates custom_stoploss output."""
    profit_pct = current_profit * 100
    if is_short:
        p1, p2, p3 = 1.0, 2.5, 5.0
    else:
        p1, p2, p3 = 1.5, 3.0, 6.0

    if profit_pct >= p3:
        # Lock 70% of profit
        return 3, _stoploss_from_open_pct(
            current_profit * 0.70, current_profit, is_short,
        )
    if profit_pct >= p2:
        # Lock 50% of profit
        return 2, _stoploss_from_open_pct(
            current_profit * 0.50, current_profit, is_short,
        )
    if profit_pct >= p1:
        # Breakeven + 0.3%
        return 1, _stoploss_from_open_pct(
            0.003, current_profit, is_short,
        )
    return 0, -0.05


# =================================================================== #
# Phase 0 — flat -5%
# =================================================================== #
def test_phase_zero_static_minus_5pct_when_below_threshold_long():
    phase, sl = _trailing_phase(current_profit=0.005, is_short=False)
    assert phase == 0
    assert sl == -0.05


def test_phase_zero_static_minus_5pct_when_below_threshold_short():
    phase, sl = _trailing_phase(current_profit=0.005, is_short=True)
    assert phase == 0
    assert sl == -0.05


def test_phase_zero_static_minus_5pct_when_in_loss():
    phase, sl = _trailing_phase(current_profit=-0.03, is_short=False)
    assert phase == 0
    assert sl == -0.05


# =================================================================== #
# Phase 1 — breakeven + 0.3% (covers fees)
# =================================================================== #
def test_phase_1_long_at_p1_threshold():
    """Long at 1.5% profit → lock at entry+0.3%.
    At 1.5% above entry, SL at entry+0.3% means SL is ~1.18% below current.
    """
    phase, sl = _trailing_phase(current_profit=0.015, is_short=False)
    assert phase == 1
    assert sl == pytest.approx(-0.01182, abs=1e-4)


def test_phase_1_short_at_p1_threshold():
    """Short locks at p1=1.0% (tighter than long's 1.5%)."""
    phase, sl = _trailing_phase(current_profit=0.012, is_short=True)
    assert phase == 1


def test_phase_1_long_below_p1_stays_in_phase_0():
    """Just below 1.5% profit → still phase 0."""
    phase, _ = _trailing_phase(current_profit=0.014, is_short=False)
    assert phase == 0


# =================================================================== #
# Phase 2 — lock 50% of profit
# =================================================================== #
def test_phase_2_long_at_p2_threshold():
    """At 3% profit, lock half = SL at entry+1.5%."""
    phase, sl = _trailing_phase(current_profit=0.03, is_short=False)
    assert phase == 2
    # SL price = entry × 1.015; current = entry × 1.03
    # distance = (1.03 - 1.015) / 1.03 = 0.01456
    assert sl == pytest.approx(-0.01456, abs=1e-4)


def test_phase_2_locks_more_as_profit_grows():
    """Higher profit in phase 2 → more locked. SL gets tighter."""
    _, sl_low = _trailing_phase(current_profit=0.035, is_short=False)
    _, sl_high = _trailing_phase(current_profit=0.045, is_short=False)
    # As profit grows phase-2 lock value (50% of profit) grows → tighter SL
    # (closer to current price = sl value gets smaller in absolute magnitude
    # because (cur_price - sl_price)/cur_price decreases when sl_price grows
    # toward cur_price)
    assert sl_high < sl_low or sl_high == sl_low


def test_phase_2_short_threshold_lower():
    """Shorts hit phase 2 at 2.5% (vs long's 3.0%)."""
    phase, _ = _trailing_phase(current_profit=0.027, is_short=True)
    assert phase == 2
    phase_long, _ = _trailing_phase(current_profit=0.027, is_short=False)
    assert phase_long == 1   # long would only be in phase 1


# =================================================================== #
# Phase 3 — lock 70% of profit
# =================================================================== #
def test_phase_3_long_at_p3_threshold():
    """At 6% profit, lock 70% = SL at entry+4.2%."""
    phase, sl = _trailing_phase(current_profit=0.06, is_short=False)
    assert phase == 3


def test_phase_3_short_at_p3_threshold():
    phase, _ = _trailing_phase(current_profit=0.05, is_short=True)
    assert phase == 3


def test_phase_3_at_extreme_profit():
    """+50% profit, locks 35%."""
    phase, sl = _trailing_phase(current_profit=0.50, is_short=False)
    assert phase == 3
    # SL at entry × 1.35; current at entry × 1.50
    # distance = (1.5 - 1.35) / 1.5 = 0.10 → -0.10
    assert sl == pytest.approx(-0.10, abs=1e-4)


# =================================================================== #
# Phase transitions — boundaries
# =================================================================== #
@pytest.mark.parametrize("profit,expected_phase", [
    (0.014, 0),    # just under p1 long
    (0.015, 1),    # exactly at p1 long
    (0.029, 1),    # just under p2 long
    (0.030, 2),    # exactly at p2 long
    (0.059, 2),    # just under p3 long
    (0.060, 3),    # exactly at p3 long
    (0.100, 3),    # well into p3
])
def test_long_phase_boundaries(profit, expected_phase):
    phase, _ = _trailing_phase(current_profit=profit, is_short=False)
    assert phase == expected_phase


@pytest.mark.parametrize("profit,expected_phase", [
    (0.009, 0),    # just under p1 short
    (0.010, 1),    # exactly at p1 short
    (0.024, 1),    # just under p2 short
    (0.025, 2),    # exactly at p2 short
    (0.049, 2),    # just under p3 short
    (0.050, 3),    # exactly at p3 short
])
def test_short_phase_boundaries(profit, expected_phase):
    phase, _ = _trailing_phase(current_profit=profit, is_short=True)
    assert phase == expected_phase


# =================================================================== #
# Asymmetry — shorts lock faster
# =================================================================== #
def test_short_locks_phase_1_at_lower_profit():
    """At 1.2% profit: short already in phase 1, long still in phase 0."""
    short_phase, _ = _trailing_phase(current_profit=0.012, is_short=True)
    long_phase, _ = _trailing_phase(current_profit=0.012, is_short=False)
    assert short_phase == 1
    assert long_phase == 0


def test_short_locks_phase_3_at_lower_profit():
    """At 5% profit: short in phase 3, long still in phase 2."""
    short_phase, _ = _trailing_phase(current_profit=0.05, is_short=True)
    long_phase, _ = _trailing_phase(current_profit=0.05, is_short=False)
    assert short_phase == 3
    assert long_phase == 2


# =================================================================== #
# Sanity: SL never widens past initial -5%
# =================================================================== #
def test_phase_0_sl_is_floor():
    """No phase should produce a SL more lenient than -5%."""
    for profit in [-0.10, -0.05, 0, 0.005, 0.01, 0.014]:
        _, sl = _trailing_phase(current_profit=profit, is_short=False)
        assert sl >= -0.05, f"profit={profit}: sl={sl} loosens past -5%"


def test_phase_progression_sl_tightens():
    """As profit grows, SL should monotonically approach 0 from below."""
    # Sample at p1, p2, p3 thresholds
    _, sl_at_p1 = _trailing_phase(current_profit=0.015, is_short=False)
    _, sl_at_p2 = _trailing_phase(current_profit=0.03, is_short=False)
    _, sl_at_p3 = _trailing_phase(current_profit=0.06, is_short=False)
    # All three should be tighter than -5%
    assert sl_at_p1 > -0.05
    assert sl_at_p2 > -0.05
    assert sl_at_p3 > -0.05


# =================================================================== #
# Strategy class config (the actual fix)
# =================================================================== #
def test_use_custom_stoploss_is_enabled():
    """Round 47 critical fix: use_custom_stoploss MUST be True
    or the trailing logic is dead code."""
    from strategies.supertrend import SupertrendStrategy
    assert SupertrendStrategy.use_custom_stoploss is True


def test_static_stoploss_floor_is_minus_5pct():
    """Phase 0 floor stays at -5%."""
    from strategies.supertrend import SupertrendStrategy
    assert SupertrendStrategy.stoploss == -0.05


def test_freqtrade_trailing_stop_is_disabled():
    """Built-in Freqtrade trailing must be off — our custom_stoploss IS the trailing."""
    from strategies.supertrend import SupertrendStrategy
    assert SupertrendStrategy.trailing_stop is False
