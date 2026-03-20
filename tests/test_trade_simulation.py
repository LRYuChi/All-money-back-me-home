"""End-to-end trade simulation tests with real-world numbers.

Verifies that the full guard pipeline + position sizing produces
trades that actually pass all guards.
"""

from guards.base import GuardContext, GuardPipeline
from guards.guards import (
    MaxPositionGuard,
    MaxLeverageGuard,
    LiquidationGuard,
    TotalExposureGuard,
)


def build_pipeline():
    """Build the same pipeline as production."""
    return GuardPipeline([
        MaxPositionGuard(max_pct=30, confident_pct=45, confidence_threshold=0.7),
        MaxLeverageGuard(max_leverage=5),
        LiquidationGuard(min_distance_mult=2.0, maintenance_margin_rate=0.01),
        TotalExposureGuard(max_pct=80),
    ])


def simulate_leverage(confidence: float, max_leverage: float = 4.983) -> float:
    """Replicate strategy leverage() formula."""
    return 1.0 + (max_leverage - 1.0) * (confidence ** 2)


def simulate_stake(
    confidence: float,
    proposed_stake: float,
    account_balance: float,
    atr: float,
    price: float,
    atr_sl_mult: float = 1.315,
) -> float:
    """Replicate custom_stake_amount() position sizing chain."""
    # Base scale
    scale = 0.2 + 1.3 * confidence
    adjusted = proposed_stake * scale

    # Risk cap (2% of account)
    atr_sl_pct = max((atr * atr_sl_mult) / price, 0.003)
    max_risk = (account_balance * 0.02) / atr_sl_pct
    adjusted = min(adjusted, max_risk)

    # Pre-limit for MaxPositionGuard
    est_lev = simulate_leverage(confidence)
    if confidence >= 0.7:
        t = min((confidence - 0.7) / 0.3, 1.0)
        eff_pct = 30.0 + (45.0 - 30.0) * t
    else:
        eff_pct = 30.0
    max_pos_value = account_balance * (eff_pct / 100)
    max_stake_guard = max_pos_value / est_lev if est_lev > 0 else max_pos_value
    adjusted = min(adjusted, max_stake_guard)

    return adjusted


# ===================================================================
# Simulation scenarios using real market data (2026-03-20)
# ===================================================================

class TestETHSimulation:
    """ETH/USDT with real values from live system."""

    ACCOUNT = 1000.0
    PRICE = 2145.0
    ATR = 10.56
    CONFIDENCE = 0.598
    PROPOSED_STAKE = 330.0  # 1000 * 0.99 / 3

    def test_stake_sizing_within_guard_limit(self):
        stake = simulate_stake(
            self.CONFIDENCE, self.PROPOSED_STAKE, self.ACCOUNT,
            self.ATR, self.PRICE,
        )
        lev = simulate_leverage(self.CONFIDENCE)
        position_value = stake * lev
        max_allowed = self.ACCOUNT * 0.30  # confidence < 0.7 → 30%
        assert position_value <= max_allowed * 1.001, (
            f"position_value {position_value:.2f} exceeds 30% limit {max_allowed:.2f}"
        )

    def test_full_pipeline_passes(self):
        stake = simulate_stake(
            self.CONFIDENCE, self.PROPOSED_STAKE, self.ACCOUNT,
            self.ATR, self.PRICE,
        )
        lev = simulate_leverage(self.CONFIDENCE)
        pipeline = build_pipeline()
        ctx = GuardContext(
            symbol="ETH/USDT:USDT",
            side="long",
            amount=stake,
            leverage=lev,
            account_balance=self.ACCOUNT,
            confidence=self.CONFIDENCE,
        )
        result = pipeline.run(ctx)
        assert result is None, f"Guard rejected: {result}"

    def test_position_value_is_reasonable(self):
        stake = simulate_stake(
            self.CONFIDENCE, self.PROPOSED_STAKE, self.ACCOUNT,
            self.ATR, self.PRICE,
        )
        lev = simulate_leverage(self.CONFIDENCE)
        pos_val = stake * lev
        # Should be between $50 and $300 for a $1000 account
        assert 50 <= pos_val <= 300, f"Position value {pos_val:.2f} out of range"


class TestBTCSimulation:
    """BTC/USDT with real values."""

    ACCOUNT = 1000.0
    PRICE = 70542.0
    ATR = 251.43
    CONFIDENCE = 0.609

    def test_full_pipeline_passes(self):
        proposed = self.ACCOUNT * 0.99 / 3
        stake = simulate_stake(
            self.CONFIDENCE, proposed, self.ACCOUNT,
            self.ATR, self.PRICE,
        )
        lev = simulate_leverage(self.CONFIDENCE)
        pipeline = build_pipeline()
        ctx = GuardContext(
            symbol="BTC/USDT:USDT",
            side="long",
            amount=stake,
            leverage=lev,
            account_balance=self.ACCOUNT,
            confidence=self.CONFIDENCE,
        )
        assert pipeline.run(ctx) is None


class TestHighConfidenceScenario:
    """High confidence (0.85) → relaxed MaxPositionGuard."""

    ACCOUNT = 1000.0
    CONFIDENCE = 0.85

    def test_effective_pct_is_37_5(self):
        """At confidence 0.85, effective pct should be ~37.5%."""
        t = min((0.85 - 0.7) / 0.3, 1.0)
        eff_pct = 30.0 + (45.0 - 30.0) * t
        assert abs(eff_pct - 37.5) < 0.01

    def test_larger_position_allowed(self):
        lev = simulate_leverage(self.CONFIDENCE)  # ~3.88x
        # With 37.5%, max position = $375, stake = $375/3.88 ≈ $96.6
        stake = simulate_stake(self.CONFIDENCE, 330, self.ACCOUNT, 250, 70000)
        pipeline = build_pipeline()
        ctx = GuardContext(
            symbol="BTC/USDT:USDT",
            side="long",
            amount=stake,
            leverage=lev,
            account_balance=self.ACCOUNT,
            confidence=self.CONFIDENCE,
        )
        assert pipeline.run(ctx) is None
        # Position should be bigger than low-confidence
        pos_val = stake * lev
        assert pos_val > 300, f"High-conf position {pos_val:.2f} should exceed $300"


class TestMultiPositionExposure:
    """Three simultaneous positions shouldn't breach TotalExposureGuard."""

    ACCOUNT = 1000.0
    CONFIDENCE = 0.65

    def test_three_positions_within_80pct(self):
        lev = simulate_leverage(self.CONFIDENCE)  # ~2.68x
        stake = simulate_stake(self.CONFIDENCE, 330, self.ACCOUNT, 250, 70000)
        pos_val = stake * lev

        pipeline = build_pipeline()
        # Third trade with 2 existing positions
        existing = {
            "BTC/USDT:USDT": {"value": pos_val},
            "ETH/USDT:USDT": {"value": pos_val},
        }
        ctx = GuardContext(
            symbol="SOL/USDT:USDT",
            side="long",
            amount=stake,
            leverage=lev,
            account_balance=self.ACCOUNT,
            open_positions=existing,
            confidence=self.CONFIDENCE,
        )
        result = pipeline.run(ctx)
        # 3 × ~$300 = $900, TotalExposure 80% = $800
        # With pre-limit, each pos ≤ $300, total ≤ $900
        # This MAY be rejected by TotalExposureGuard — verify
        total = pos_val * 2 + stake * lev
        if total <= 800:
            assert result is None
        else:
            assert result is not None
            assert "Total exposure" in result


class TestSmallAccountLeverage:
    """Small $300 account gets lower leverage limits."""

    def test_leverage_capped_for_small_account(self):
        pipeline = build_pipeline()
        ctx = GuardContext(
            symbol="BTC/USDT:USDT",
            side="long",
            amount=30,
            leverage=3.0,
            account_balance=300,
            confidence=0.6,
        )
        result = pipeline.run(ctx)
        # MaxLeverageGuard: dynamic_max = 1.5 + 3.5 * 0.3 = 2.55
        # 3.0 > 2.55 → REJECT
        assert result is not None
        assert "Leverage" in result
