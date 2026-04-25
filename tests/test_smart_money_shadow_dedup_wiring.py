"""Tests for shadow daemon dedup wiring (round 45).

Focus: the CLI parser exposes the new --intent-dedup-window-sec flag and
the _build_deduper factory selects the right type based on the value.
We don't construct the full StrategyRuntime here (heavy deps); see
test_strategy_runtime_disabled_honor + test_execution_intent_dedup for
the rest of the chain.
"""
from __future__ import annotations

import pytest

from execution.pending_orders import (
    NoOpIntentDeduper,
    WindowedIntentDeduper,
)
from smart_money.cli.shadow import _build_deduper, build_parser


# ================================================================== #
# CLI parser exposes the new flag
# ================================================================== #
def test_parser_default_dedup_window_sixty_seconds():
    """Sensible default: 60s catches double-fires from WS reconnects,
    aggregator double-counts, tight-cluster wallet events."""
    args = build_parser().parse_args([])
    assert args.intent_dedup_window_sec == 60.0


def test_parser_accepts_custom_dedup_window():
    args = build_parser().parse_args(["--intent-dedup-window-sec", "120"])
    assert args.intent_dedup_window_sec == 120.0


def test_parser_accepts_zero_dedup_window():
    """0 must be accepted as 'dedup disabled'."""
    args = build_parser().parse_args(["--intent-dedup-window-sec", "0"])
    assert args.intent_dedup_window_sec == 0.0


def test_parser_accepts_fractional_seconds():
    """For high-frequency strategies."""
    args = build_parser().parse_args(["--intent-dedup-window-sec", "2.5"])
    assert args.intent_dedup_window_sec == 2.5


# ================================================================== #
# _build_deduper factory selects right type
# ================================================================== #
def test_build_deduper_zero_returns_noop():
    d = _build_deduper(0)
    assert isinstance(d, NoOpIntentDeduper)


def test_build_deduper_negative_returns_noop():
    """Negative window is forgiving: treat as disabled rather than crashing."""
    d = _build_deduper(-5.0)
    assert isinstance(d, NoOpIntentDeduper)


def test_build_deduper_positive_returns_windowed():
    d = _build_deduper(60.0)
    assert isinstance(d, WindowedIntentDeduper)


def test_build_deduper_window_value_propagates():
    """The constructed deduper actually uses the requested window."""
    from datetime import datetime, timedelta, timezone
    from shared.signals.types import Direction, FusedSignal, StrategyIntent

    d = _build_deduper(30.0)

    base = datetime(2026, 4, 25, 12, 0, tzinfo=timezone.utc)
    fused = FusedSignal(
        symbol="X", horizon="1h", direction=Direction.LONG,
        ensemble_score=0.7, regime="BULL", sources_count=1,
        contributions={"smart_money": 0.7}, conflict=False,
    )

    def make_intent(ts):
        return StrategyIntent(
            strategy_id="s", symbol="X", direction=Direction.LONG,
            target_notional_usd=100, entry_price_ref=None,
            stop_loss_pct=None, take_profit_pct=None,
            source_fused=fused, ts=ts,
        )

    d.record(make_intent(base))
    # 20s later — within window
    assert d.is_duplicate(make_intent(base + timedelta(seconds=20)))
    # 35s later — outside window (window=30)
    assert not d.is_duplicate(make_intent(base + timedelta(seconds=35)))


def test_build_deduper_fractional_window_works():
    """0.5s window for HFT strategies — exercises float arithmetic."""
    d = _build_deduper(0.5)
    assert isinstance(d, WindowedIntentDeduper)


# ================================================================== #
# E2E: parser → factory chain produces the right deduper
# ================================================================== #
def test_e2e_parser_to_factory_chain():
    """Flag value flows through to deduper instance."""
    args = build_parser().parse_args(["--intent-dedup-window-sec", "180"])
    d = _build_deduper(args.intent_dedup_window_sec)
    assert isinstance(d, WindowedIntentDeduper)


def test_e2e_default_chain_has_dedup_enabled():
    """Default flow (no --intent-dedup-window-sec passed) gives a real
    Windowed deduper, not NoOp. Important: dedup must be ON by default
    so deployments without explicit opt-in are protected."""
    args = build_parser().parse_args([])
    d = _build_deduper(args.intent_dedup_window_sec)
    assert isinstance(d, WindowedIntentDeduper)


def test_e2e_explicit_disable_chain():
    args = build_parser().parse_args(["--intent-dedup-window-sec", "0"])
    d = _build_deduper(args.intent_dedup_window_sec)
    assert isinstance(d, NoOpIntentDeduper)
