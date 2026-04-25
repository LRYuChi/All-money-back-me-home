"""Tests for R73 — _build_shadow_alerts helper in api router.

Synthesizes the `density / latency / skipped_reasons / positions / health`
inputs the helper receives and asserts each alert rule fires (or stays
silent) per spec.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Bootstrap apps/api on sys.path so we can import the router
_API_SRC = Path(__file__).resolve().parent.parent / "apps" / "api"
if str(_API_SRC) not in sys.path:
    sys.path.insert(0, str(_API_SRC))

from src.routers.smart_money import _build_shadow_alerts


def _healthy_inputs(**overrides) -> dict:
    """Default inputs that should produce zero alerts."""
    base = {
        "health": "green",
        "health_reason": None,
        "density": {
            "1h": {"paper_open": 1, "paper_closed": 2, "skipped": 3},
            "6h": {"paper_open": 5, "paper_closed": 10, "skipped": 8},
            "24h": {"paper_open": 8, "paper_closed": 30, "skipped": 20},
        },
        "latency": {"n": 30, "p50_ms": 1500, "p95_ms": 8000, "p99_ms": 12000},
        "skipped_reasons": {"unknown_symbol": 10, "freshness_filter": 5},
        "positions": {"long": 4, "short": 3, "flat": 5,
                      "distinct_wallets": 12},
    }
    base.update(overrides)
    return base


def test_healthy_pipeline_produces_no_alerts():
    assert _build_shadow_alerts(**_healthy_inputs()) == []


# =================================================================== #
# RED_PIPELINE
# =================================================================== #
def test_red_pipeline_fires_red_alert():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            health="red", health_reason="no pipeline activity in 24h",
        ),
    )
    assert any("RED_PIPELINE" in a for a in alerts)


def test_yellow_pipeline_no_red_alert():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(health="yellow", health_reason="silent 1h"),
    )
    assert not any("RED_PIPELINE" in a for a in alerts)


# =================================================================== #
# LATENCY_BUDGET_EXCEEDED
# =================================================================== #
def test_latency_budget_alert_fires_at_high_p95():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            latency={"n": 30, "p50_ms": 5000, "p95_ms": 20_000, "p99_ms": 25_000},
        ),
    )
    assert any("LATENCY_BUDGET_EXCEEDED" in a and "20000ms" in a for a in alerts)


def test_latency_budget_silent_at_acceptable_p95():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            latency={"n": 30, "p50_ms": 1500, "p95_ms": 14_000, "p99_ms": 16_000},
        ),
    )
    assert not any("LATENCY_BUDGET" in a for a in alerts)


def test_latency_budget_silent_when_p95_none():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            latency={"n": 0, "p50_ms": None, "p95_ms": None, "p99_ms": None},
        ),
    )
    assert not any("LATENCY_BUDGET" in a for a in alerts)


# =================================================================== #
# COLD_START_DRIFT_DOMINANT (the R72-driven rule)
# =================================================================== #
def test_cold_start_dominant_alert_fires():
    """Mirrors the user's screenshot: 66/66 cold_start_drift."""
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            skipped_reasons={"cold_start_drift": 66},
        ),
    )
    assert any("COLD_START_DRIFT_DOMINANT" in a and "66/66" in a for a in alerts)
    # Suggests R72 warmup remediation
    assert any("R72" in a or "warmup" in a for a in
               (a for a in alerts if "COLD_START" in a))


def test_cold_start_dominant_silent_below_threshold():
    """< 50% of skips → no alert."""
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            skipped_reasons={
                "cold_start_drift": 10,
                "unknown_symbol": 50,
            },
        ),
    )
    assert not any("COLD_START_DRIFT_DOMINANT" in a for a in alerts)


def test_cold_start_dominant_silent_at_low_volume():
    """Only 5 total skips → not enough sample to trigger."""
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            skipped_reasons={"cold_start_drift": 5},
        ),
    )
    assert not any("COLD_START_DRIFT_DOMINANT" in a for a in alerts)


# =================================================================== #
# ALL_SKIPPED_NO_PAPER
# =================================================================== #
def test_all_skipped_no_paper_alert_fires():
    """Many skips + zero paper trades in 1h → broken pipeline downstream."""
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            density={
                "1h": {"paper_open": 0, "paper_closed": 0, "skipped": 25},
                "6h": {"paper_open": 0, "paper_closed": 0, "skipped": 100},
                "24h": {"paper_open": 0, "paper_closed": 0, "skipped": 400},
            },
        ),
    )
    assert any("ALL_SKIPPED_NO_PAPER" in a for a in alerts)


def test_all_skipped_no_paper_silent_when_some_paper_trades():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            density={
                "1h": {"paper_open": 1, "paper_closed": 0, "skipped": 25},
                "6h": {"paper_open": 5, "paper_closed": 2, "skipped": 100},
                "24h": {"paper_open": 8, "paper_closed": 5, "skipped": 400},
            },
        ),
    )
    assert not any("ALL_SKIPPED_NO_PAPER" in a for a in alerts)


def test_all_skipped_no_paper_silent_at_low_skip_count():
    """Only a few skips → too small to declare anomaly."""
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            density={
                "1h": {"paper_open": 0, "paper_closed": 0, "skipped": 5},
                "6h": {"paper_open": 0, "paper_closed": 0, "skipped": 20},
                "24h": {"paper_open": 0, "paper_closed": 0, "skipped": 80},
            },
        ),
    )
    assert not any("ALL_SKIPPED_NO_PAPER" in a for a in alerts)


# =================================================================== #
# ZERO_TRADEABLE_WALLETS
# =================================================================== #
def test_zero_tradeable_wallets_alert_fires():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            positions={"long": 0, "short": 0, "flat": 0,
                       "distinct_wallets": 0},
        ),
    )
    assert any("ZERO_TRADEABLE_WALLETS" in a for a in alerts)


def test_zero_tradeable_wallets_silent_with_any_wallet():
    alerts = _build_shadow_alerts(
        **_healthy_inputs(
            positions={"long": 0, "short": 0, "flat": 1,
                       "distinct_wallets": 1},
        ),
    )
    assert not any("ZERO_TRADEABLE_WALLETS" in a for a in alerts)


# =================================================================== #
# Multiple alerts can coexist
# =================================================================== #
def test_multiple_alerts_can_fire_simultaneously():
    """Worst-case scenario — every rule triggered."""
    alerts = _build_shadow_alerts(
        health="red",
        health_reason="no 24h activity",
        density={
            "1h": {"paper_open": 0, "paper_closed": 0, "skipped": 25},
            "6h": {"paper_open": 0, "paper_closed": 0, "skipped": 100},
            "24h": {"paper_open": 0, "paper_closed": 0, "skipped": 400},
        },
        latency={"n": 5, "p50_ms": 30_000, "p95_ms": 50_000, "p99_ms": 60_000},
        skipped_reasons={"cold_start_drift": 350, "unknown_symbol": 50},
        positions={"long": 0, "short": 0, "flat": 0,
                   "distinct_wallets": 0},
    )
    # All 5 rules
    assert any("RED_PIPELINE" in a for a in alerts)
    assert any("LATENCY_BUDGET" in a for a in alerts)
    assert any("COLD_START_DRIFT_DOMINANT" in a for a in alerts)
    assert any("ALL_SKIPPED_NO_PAPER" in a for a in alerts)
    assert any("ZERO_TRADEABLE_WALLETS" in a for a in alerts)
    assert len(alerts) >= 5
