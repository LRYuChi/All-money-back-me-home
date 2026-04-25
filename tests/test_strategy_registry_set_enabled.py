"""Tests for set_enabled + enable_history (round 25).

Covers InMemoryStrategyRegistry behaviour. Postgres + Supabase backends
share the same Protocol so a chain of unit tests on InMemory plus type
contracts is enough — full DB integration tested manually via migration
021 apply (see roadmap §G).
"""
from __future__ import annotations

from datetime import datetime, timezone
from time import sleep

import pytest

from strategy_engine import (
    EnableEvent,
    InMemoryStrategyRegistry,
    StrategyNotFound,
)


VALID_YAML = """
id: test_se_v1
market: crypto
symbol: BTC
timeframe: 1h
enabled: true
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
"""

DISABLED_YAML = """
id: test_se_v2
market: crypto
symbol: BTC
timeframe: 1h
enabled: false
entry:
  long:
    all_of:
      - 'fused.direction == "long"'
"""


# ================================================================== #
# set_enabled — basic flips
# ================================================================== #
def test_set_enabled_disables_active_strategy():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    rec = reg.set_enabled("test_se_v1", False, reason="test", actor="test_runner")
    assert rec.parsed.enabled is False


def test_set_enabled_enables_disabled_strategy():
    reg = InMemoryStrategyRegistry()
    reg.upsert(DISABLED_YAML)
    assert reg.get("test_se_v2").parsed.enabled is False
    rec = reg.set_enabled("test_se_v2", True, reason="manual unlock")
    assert rec.parsed.enabled is True


def test_set_enabled_unknown_id_raises_not_found():
    reg = InMemoryStrategyRegistry()
    with pytest.raises(StrategyNotFound):
        reg.set_enabled("does_not_exist", False)


def test_set_enabled_idempotent_for_same_state():
    """Setting enabled to its current value still records an audit row —
    intentional: humans want a paper trail of the no-op too."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", True, reason="confirm green")
    assert reg.get("test_se_v1").parsed.enabled is True
    history = reg.enable_history("test_se_v1")
    assert len(history) == 1
    assert history[0].enabled is True


def test_set_enabled_updates_updated_at():
    reg = InMemoryStrategyRegistry()
    rec0 = reg.upsert(VALID_YAML)
    sleep(0.001)
    rec1 = reg.set_enabled("test_se_v1", False)
    assert rec1.updated_at > rec0.updated_at


def test_set_enabled_preserves_yaml_text():
    """We don't rewrite YAML on flip — the raw text stays for replay."""
    reg = InMemoryStrategyRegistry()
    rec0 = reg.upsert(VALID_YAML)
    rec1 = reg.set_enabled("test_se_v1", False)
    assert rec1.yaml_text == rec0.yaml_text


# ================================================================== #
# Persistence across get / list_active
# ================================================================== #
def test_disabled_strategy_excluded_from_list_active():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False)
    assert reg.list_active() == []


def test_disabled_strategy_still_in_list_all():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False)
    all_recs = reg.list_all()
    assert len(all_recs) == 1
    assert all_recs[0].parsed.enabled is False


def test_get_after_set_enabled_reflects_new_state():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False, reason="g9 trip")
    rec = reg.get("test_se_v1")
    assert rec.parsed.enabled is False


def test_yaml_re_upsert_does_not_resurrect_disabled():
    """Critical: G9 disables a strategy. Then someone re-uploads YAML
    with `enabled: true` (the source-of-truth YAML in git). The DB-side
    disabled flag MUST win — otherwise a routine YAML push would silently
    re-enable a CB-tripped strategy."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False, reason="g9 trip")
    assert reg.get("test_se_v1").parsed.enabled is False

    # Re-upload the same YAML (which has enabled: true)
    reg.upsert(VALID_YAML)
    assert reg.get("test_se_v1").parsed.enabled is False


# ================================================================== #
# enable_history audit log
# ================================================================== #
def test_enable_history_returns_empty_for_untouched_strategy():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    assert reg.enable_history("test_se_v1") == []


def test_enable_history_records_each_flip():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False, reason="g9", actor="guard:consecutive_loss_cb")
    sleep(0.001)
    reg.set_enabled("test_se_v1", True, reason="manual", actor="human:yuchi")
    history = reg.enable_history("test_se_v1")
    assert len(history) == 2
    # newest first
    assert history[0].enabled is True
    assert history[0].actor == "human:yuchi"
    assert history[1].enabled is False
    assert history[1].actor == "guard:consecutive_loss_cb"


def test_enable_history_respects_limit():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    for i in range(5):
        reg.set_enabled("test_se_v1", i % 2 == 0, reason=f"flip {i}")
    assert len(reg.enable_history("test_se_v1", limit=3)) == 3


def test_enable_history_isolates_per_strategy():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.upsert(DISABLED_YAML)
    reg.set_enabled("test_se_v1", False, reason="A")
    reg.set_enabled("test_se_v2", True, reason="B")
    h1 = reg.enable_history("test_se_v1")
    h2 = reg.enable_history("test_se_v2")
    assert len(h1) == 1 and h1[0].reason == "A"
    assert len(h2) == 1 and h2[0].reason == "B"


def test_enable_event_has_required_fields():
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled(
        "test_se_v1", False,
        reason="3-day loss streak",
        actor="guard:consecutive_loss_cb",
    )
    ev = reg.enable_history("test_se_v1")[0]
    assert isinstance(ev, EnableEvent)
    assert ev.strategy_id == "test_se_v1"
    assert ev.enabled is False
    assert ev.reason == "3-day loss streak"
    assert ev.actor == "guard:consecutive_loss_cb"
    assert ev.created_at.tzinfo is not None


def test_enable_history_allows_null_reason_actor():
    """Caller may not always have a reason — schema allows NULL."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False)
    ev = reg.enable_history("test_se_v1")[0]
    assert ev.reason is None
    assert ev.actor is None


# ================================================================== #
# Delete cleans up
# ================================================================== #
def test_delete_clears_enabled_override():
    """If we delete and re-upsert the same id, the new strategy starts
    fresh (no inherited disabled state from before delete)."""
    reg = InMemoryStrategyRegistry()
    reg.upsert(VALID_YAML)
    reg.set_enabled("test_se_v1", False)
    reg.delete("test_se_v1")
    reg.upsert(VALID_YAML)   # YAML says enabled: true
    assert reg.get("test_se_v1").parsed.enabled is True
