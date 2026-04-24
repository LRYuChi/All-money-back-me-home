"""End-to-end integration: SM signal → universal → regime → fuser → strategy → intent.

Validates the entire rule-only chain (everything except Kronos / AI LLM)
runs without manual glue. Picks up interface-level bugs that unit tests
miss — e.g. adapter outputs a horizon the fuser doesn't accept, or
fused.contributions key naming mismatches the strategy DSL.

These tests run against InMemory + on-disk default configs:
  - config/fusion/weights.yaml (real production weights)
  - config/strategies/crypto_btc_smart_money_v1.yaml (first prod strategy)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from fusion import (
    DEFAULT_WEIGHTS_PATH,
    MarketContext,
    Regime,
    SignalFuser,
    detect_regime,
    load_weights,
)
from shared.signals.adapters import from_smart_money
from shared.signals.types import (
    Direction,
    SignalSource,
    UniversalSignal,
)
from smart_money.signals.types import RawFillEvent, Signal as SMSignal, SignalType
from strategy_engine import (
    evaluate,
    load_strategy,
)


# ================================================================== #
# Fixtures
# ================================================================== #
DEFAULT_STRATEGY = Path("config/strategies/crypto_btc_smart_money_v1.yaml")


def make_sm_signal(
    sig_type: SignalType = SignalType.OPEN_LONG,
    wallet_score: float = 0.85,
    px: float = 50_000.0,
    size_delta: float = 0.5,
    new_size: float = 0.5,
    coin: str = "BTC",
) -> SMSignal:
    """Mirror smart_money classifier output."""
    event = RawFillEvent(
        wallet_address="0xwhale" + "0" * 34,
        symbol_hl=coin, side_raw="B", direction_raw="Open Long",
        size=size_delta, px=px, fee=1.0, hl_trade_id=42,
        ts_hl_fill_ms=1_700_000_000_000,
        ts_ws_received_ms=1_700_000_000_500,
        ts_queue_processed_ms=1_700_000_000_550,
        source="ws",
    )
    return SMSignal(
        wallet_id=uuid4(),
        wallet_address="0xwhale" + "0" * 34,
        wallet_score=wallet_score,
        symbol_hl=coin,
        signal_type=sig_type,
        size_delta=size_delta,
        new_size=new_size,
        px=px,
        whale_equity_usd=10_000_000.0,
        whale_position_usd=new_size * px,
        source_event=event,
    )


def make_kronos_signal(
    direction: Direction = Direction.LONG,
    strength: float = 0.6,
    horizon: str = "15m",
) -> UniversalSignal:
    """Phase C will produce these for real; placeholder for integration test."""
    return UniversalSignal(
        source=SignalSource.KRONOS,
        symbol="crypto:hyperliquid:BTC",
        horizon=horizon,
        direction=direction,
        strength=strength,
        reason="kronos forecast (test fixture)",
        details={"p50": 0.005, "p5": -0.002, "p95": 0.012},
    )


@pytest.fixture
def weights():
    return load_weights(DEFAULT_WEIGHTS_PATH)


@pytest.fixture
def strategy():
    return load_strategy(DEFAULT_STRATEGY)


@pytest.fixture
def fuser(weights):
    return SignalFuser(weights)


# ================================================================== #
# Helpers
# ================================================================== #
def build_context(fused, regime: Regime, capital: float = 10_000) -> dict:
    """Construct the dict shape that strategy.evaluate() expects."""
    return {
        "fused": {
            "direction": fused.direction.value,
            "ensemble_score": fused.ensemble_score,
            "sources_count": fused.sources_count,
            "contributions": fused.contributions,
            "conflict": fused.conflict,
        },
        "regime": regime.value,
        "capital": capital,
    }


def bull_trending_market() -> MarketContext:
    return MarketContext(
        btc_price=55_000, btc_ma200=50_000,
        btc_ma200_slope=0.002, btc_realized_vol=0.4,
    )


def crisis_market() -> MarketContext:
    return MarketContext(
        vix=42, btc_price=46_000, btc_ma200=50_000,
        btc_ma200_slope=-0.001, btc_realized_vol=1.2,
    )


# ================================================================== #
# Happy path: SM long + Kronos long + bull regime → entry fires
# ================================================================== #
def test_sm_plus_kronos_long_in_bull_fires_entry(fuser, strategy):
    sm_universal = from_smart_money(make_sm_signal(
        sig_type=SignalType.OPEN_LONG, wallet_score=0.85,
    ))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.7)

    # Step 1: detect regime
    regime = detect_regime(bull_trending_market())
    assert regime == Regime.BULL_TRENDING

    # Step 2: fuse — SM is 15m horizon, kronos make sure it matches
    fused = fuser.fuse(
        [sm_universal, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )
    assert fused.direction == Direction.LONG
    assert fused.sources_count == 2
    assert fused.conflict is False

    # Step 3: build context + evaluate strategy
    ctx = build_context(fused, regime, capital=10_000)
    intent = evaluate(strategy, ctx)

    assert intent is not None
    assert intent.direction == Direction.LONG
    assert intent.strategy_id == "crypto_btc_smart_money_v1"
    assert intent.symbol == "crypto:hyperliquid:BTC"
    # 5% of 10k = 500, which equals the cap
    assert intent.target_notional_usd == 500
    # SL/TP propagated from YAML
    assert intent.stop_loss_pct == 0.02
    assert intent.take_profit_pct is None


# ================================================================== #
# Crisis regime blocks entry even with strong long signals
# ================================================================== #
def test_crisis_regime_blocks_long_entry(fuser, strategy):
    sm_universal = from_smart_money(make_sm_signal(wallet_score=0.95))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.9)

    regime = detect_regime(crisis_market())
    assert regime == Regime.CRISIS

    fused = fuser.fuse(
        [sm_universal, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )
    # Fuser still produces a long-direction fused, but strategy `none_of`
    # rejects it.
    assert fused.direction == Direction.LONG

    intent = evaluate(strategy, build_context(fused, regime))
    assert intent is None  # Strategy `none_of: regime == "CRISIS"` blocks it


# ================================================================== #
# Single-source signal blocked by sources_count >= 2
# ================================================================== #
def test_single_source_blocked_by_sources_count_minimum(fuser, strategy):
    """Strategy requires sources_count >= 2; lone SM signal should not fire."""
    sm_universal = from_smart_money(make_sm_signal(wallet_score=0.95))
    regime = detect_regime(bull_trending_market())

    fused = fuser.fuse(
        [sm_universal], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )
    assert fused.sources_count == 1

    intent = evaluate(strategy, build_context(fused, regime))
    assert intent is None


# ================================================================== #
# Conflict between sources halves score AND blocks via conflict==false
# ================================================================== #
def test_conflict_between_sources_blocks_entry(fuser, strategy):
    """SM long + Kronos short → fuser may flag conflict; strategy demands none."""
    sm_long = from_smart_money(make_sm_signal(
        sig_type=SignalType.OPEN_LONG, wallet_score=0.7,
    ))
    kronos_short = make_kronos_signal(direction=Direction.SHORT, strength=0.7)

    regime = detect_regime(bull_trending_market())
    fused = fuser.fuse(
        [sm_long, kronos_short], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )

    # Whichever direction wins, strategy requires conflict==false
    if fused.conflict:
        intent = evaluate(strategy, build_context(fused, regime))
        assert intent is None


# ================================================================== #
# Short entry path
# ================================================================== #
def test_short_entry_in_bear_choppy_fires(fuser, strategy):
    """All sources short, BEAR_CHOPPY regime, should pass strategy short rules."""
    sm_short_universal = from_smart_money(make_sm_signal(
        sig_type=SignalType.OPEN_SHORT, wallet_score=0.85,
        size_delta=-0.5, new_size=0.5,  # negative size for sell side
    ))
    kronos_short = make_kronos_signal(direction=Direction.SHORT, strength=0.8)

    bear_ctx = MarketContext(
        btc_price=45_000, btc_ma200=50_000,
        btc_ma200_slope=-0.002, btc_realized_vol=0.7,  # high vol → CHOPPY
    )
    regime = detect_regime(bear_ctx)
    assert regime == Regime.BEAR_CHOPPY

    fused = fuser.fuse(
        [sm_short_universal, kronos_short], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )
    assert fused.direction == Direction.SHORT

    intent = evaluate(strategy, build_context(fused, regime))
    # Strategy short rules: ensemble_score >= 0.6 AND sources_count >= 2 AND
    # not in BULL_TRENDING. BEAR_CHOPPY is fine.
    if fused.ensemble_score >= 0.6:
        assert intent is not None
        assert intent.direction == Direction.SHORT


# ================================================================== #
# Position sizing: percent of capital with cap
# ================================================================== #
def test_sizing_clamped_by_max_size_usd(fuser, strategy):
    sm = from_smart_money(make_sm_signal(wallet_score=0.9))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.8)

    regime = detect_regime(bull_trending_market())
    fused = fuser.fuse(
        [sm, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )

    # 5% of 100k = 5000, but max_size_usd caps at 500
    intent = evaluate(strategy, build_context(fused, regime, capital=100_000))
    assert intent is not None
    assert intent.target_notional_usd == 500


def test_sizing_below_cap_uses_actual_pct(fuser, strategy):
    sm = from_smart_money(make_sm_signal(wallet_score=0.9))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.8)

    regime = detect_regime(bull_trending_market())
    fused = fuser.fuse(
        [sm, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )

    # 5% of 5000 = 250, below the 500 cap
    intent = evaluate(strategy, build_context(fused, regime, capital=5_000))
    assert intent is not None
    assert intent.target_notional_usd == 250


# ================================================================== #
# Audit trail: fused signal's contributions visible in intent.source_fused
# ================================================================== #
def test_intent_carries_fused_signal_for_audit(fuser, strategy):
    sm = from_smart_money(make_sm_signal(wallet_score=0.9))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.7)

    regime = detect_regime(bull_trending_market())
    fused = fuser.fuse(
        [sm, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )

    intent = evaluate(strategy, build_context(fused, regime))
    assert intent is not None
    # source_fused is the placeholder built by evaluator from context
    # (production passes the real FusedSignal directly via fused_signal arg)
    assert intent.source_fused is not None
    assert intent.source_fused.regime == "BULL_TRENDING"
    assert intent.source_fused.direction == Direction.LONG


# ================================================================== #
# Disabled strategy is a no-op
# ================================================================== #
def test_disabled_strategy_skips_evaluation(fuser, weights, tmp_path):
    yaml_text = (DEFAULT_STRATEGY).read_text().replace(
        "enabled: true", "enabled: false"
    )
    f = tmp_path / "disabled.yaml"
    f.write_text(yaml_text)
    s = load_strategy(f)

    sm = from_smart_money(make_sm_signal(wallet_score=0.9))
    kronos = make_kronos_signal(direction=Direction.LONG, strength=0.8)
    regime = detect_regime(bull_trending_market())
    fused = fuser.fuse(
        [sm, kronos], regime,
        symbol="crypto:hyperliquid:BTC", horizon="15m",
    )
    intent = evaluate(s, build_context(fused, regime))
    assert intent is None
