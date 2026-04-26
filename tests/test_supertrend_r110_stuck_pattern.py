"""R110 — closest_to_fire skips disabled tiers + STRONG_TREND_NO_FIRES alert.

Real-world bug found 2026-04-26 evening: 6 pairs aligned to confirmed
tier but blocked by R87 sentinel. Pre-R110 the dashboard showed
"distance=1, just one condition away" — misleading the operator into
thinking entry was imminent when the only remaining failure was the
permanent R87 disable sentinel. R110 fixes both:

  (a) _closest_to_fire now skips tiers whose only failure is *_disabled_*
  (b) new STRONG_TREND_NO_FIRES alert detects the stuck pattern at the
      operations-level + suggests options
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "apps", "api", "src"))

from routers.supertrend import _closest_to_fire, _build_ops_alerts   # noqa: E402


# =================================================================== #
# (a) _closest_to_fire skips disabled tiers
# =================================================================== #

def test_disabled_confirmed_tier_is_skipped_for_scout():
    """Pre-R110 returned confirmed dist=1; R110 surfaces real next tier."""
    ev = {
        "confirmed_failures": ["confirmed_disabled_R87"],   # R87 sentinel
        "scout_failures": ["bull_just_formed=False", "quality<=0.5"],
        "pre_scout_failures": ["pair_bullish_2tf_just_formed=False"],
    }
    out = _closest_to_fire(ev)
    assert out["tier"] == "pre_scout"   # 1 failure, skipping disabled confirmed
    assert out["fire_distance"] == 1


def test_disabled_confirmed_skipped_even_when_scout_fewer_failures():
    """If confirmed_disabled is the only confirmed failure (1 entry),
    it should NOT win even though it has the lowest count — it's permanent."""
    ev = {
        "confirmed_failures": ["confirmed_disabled_R87"],         # 1 (but dead)
        "scout_failures": ["bull_just_formed=False", "quality<=0.5"],   # 2
        "pre_scout_failures": ["a", "b", "c"],
    }
    out = _closest_to_fire(ev)
    assert out["tier"] == "scout"   # NOT confirmed
    assert "confirmed_disabled" not in (out["remaining"][0] or "")


def test_disabled_tier_with_other_failures_still_counts():
    """Sanity: a tier with disabled sentinel PLUS other failures isn't
    'permanently disabled' from this fn's POV — it's just usual fail-count.
    The sentinel-only special-case is the one we skip."""
    ev = {
        "confirmed_failures": ["confirmed_disabled_R87", "vol<=1*ma"],   # 2
        "scout_failures": ["a", "b", "c"],   # 3
        "pre_scout_failures": ["a", "b", "c", "d"],   # 4
    }
    out = _closest_to_fire(ev)
    assert out["tier"] == "confirmed"   # 2 < 3 < 4
    assert out["fire_distance"] == 2


def test_all_tiers_disabled_returns_explicit_marker():
    """When literally every tier is dead, return a sentinel result so
    operator sees explicit 'all tiers exhausted' rather than missing data."""
    ev = {
        "confirmed_failures": ["confirmed_disabled_R87"],
        "scout_failures": ["scout_disabled_R99"],
        "pre_scout_failures": ["pre_scout_disabled_RXX"],
    }
    out = _closest_to_fire(ev)
    assert out["tier"] is None
    assert out["fire_distance"] == 999
    assert "all_tiers_disabled" in out["remaining"][0]


def test_fired_tier_overrides_disabled_logic():
    """If any tier already fired, return that — disabled-skipping doesn't apply."""
    ev = {
        "confirmed_fired": True,
        "confirmed_failures": [],
        "scout_failures": ["scout_disabled_R99"],
        "pre_scout_failures": ["any"],
    }
    out = _closest_to_fire(ev)
    assert out["tier"] == "confirmed"
    assert out["already_fired"] is True


# =================================================================== #
# (b) STRONG_TREND_NO_FIRES alert
# =================================================================== #

def test_strong_trend_alert_fires_when_R87_dominant():
    """confirmed_disabled_R87 hits dwarf the not-formed failures →
    bot is observing strong trend, R87 stuck pattern."""
    alerts = _build_ops_alerts(
        bot_state="running", n_pairs=18,
        eval_summary={
            "n_evaluations": 1500,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {
                "confirmed_disabled_R87": 270,
                "vol<=1*ma": 50,
                "bull_just_formed=False": 30,   # < 30% of confirmed_disabled
            },
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    stuck = next((a for a in alerts if "STRONG_TREND_NO_FIRES" in a), None)
    assert stuck is not None
    assert "270" in stuck
    assert "SUPERTREND_DISABLE_CONFIRMED=0" in stuck
    assert "/api/supertrend/scanner" in stuck


def test_strong_trend_alert_silent_when_alignment_dominates():
    """If alignment-not-formed dominates over R87 sentinel, that's just
    chop — strategy is correctly waiting, NOT stuck. Don't alert."""
    alerts = _build_ops_alerts(
        bot_state="running", n_pairs=18,
        eval_summary={
            "n_evaluations": 1500,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {
                "bull_just_formed=False": 1500,   # alignment dominant
                "confirmed_disabled_R87": 50,     # well below 30% of 1500
            },
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    assert not any("STRONG_TREND_NO_FIRES" in a for a in alerts)


def test_strong_trend_alert_silent_at_low_R87_count():
    """Low absolute R87 hits → not enough sample to claim 'stuck pattern'."""
    alerts = _build_ops_alerts(
        bot_state="running", n_pairs=18,
        eval_summary={
            "n_evaluations": 200,
            "tier_fired_count": {"confirmed": 0, "scout": 0, "pre_scout": 0},
            "failures_top": {
                "confirmed_disabled_R87": 30,   # < 50 threshold
                "bull_just_formed=False": 10,
            },
        },
        health={"ok": True}, recent_trades=0, journal_ok=True,
    )
    assert not any("STRONG_TREND_NO_FIRES" in a for a in alerts)


def test_strong_trend_alert_silent_when_some_fires_happened():
    """If anything actually fired, NO_FIRES_24H doesn't trip → also no STRONG_TREND."""
    alerts = _build_ops_alerts(
        bot_state="running", n_pairs=18,
        eval_summary={
            "n_evaluations": 1500,
            "tier_fired_count": {"confirmed": 0, "scout": 2, "pre_scout": 0},
            "failures_top": {"confirmed_disabled_R87": 270},
        },
        health={"ok": True}, recent_trades=2, journal_ok=True,
    )
    assert not any("STRONG_TREND_NO_FIRES" in a for a in alerts)
