"""Tests for fusion.weights + fusion.fuser."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fusion import (
    DEFAULT_WEIGHTS_PATH,
    Regime,
    SignalFuser,
    WeightsError,
    load_weights,
)
from fusion.fuser import FuserConfig
from shared.signals.types import Direction, SignalSource, UniversalSignal


# ================================================================== #
# Helpers
# ================================================================== #
def make_signal(
    source=SignalSource.SMART_MONEY,
    direction=Direction.LONG,
    strength=0.7,
    horizon="1h",
    expires_in_min: int | None = None,
    ts: datetime | None = None,
) -> UniversalSignal:
    ts = ts or datetime.now(timezone.utc)
    expires_at = ts + timedelta(minutes=expires_in_min) if expires_in_min is not None else None
    return UniversalSignal(
        source=source, symbol="X", horizon=horizon,
        direction=direction, strength=strength, reason="test",
        ts=ts, expires_at=expires_at,
    )


@pytest.fixture
def weights():
    """Use default repo weights — also exercises load_weights end-to-end."""
    return load_weights(DEFAULT_WEIGHTS_PATH)


@pytest.fixture
def fuser(weights):
    return SignalFuser(weights)


# ================================================================== #
# weights.py — load + validate
# ================================================================== #
def test_load_default_weights_succeeds():
    w = load_weights(DEFAULT_WEIGHTS_PATH)
    # Every regime present
    for r in Regime:
        assert r.value in w
    # Each row has at least one source
    for regime, sources in w.items():
        assert len(sources) > 0


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(WeightsError, match="not found"):
        load_weights(tmp_path / "nope.yaml")


def test_load_invalid_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("[\nunclosed")
    with pytest.raises(WeightsError, match="YAML parse"):
        load_weights(bad)


def test_load_unknown_regime_rejected(tmp_path):
    bad = tmp_path / "w.yaml"
    bad.write_text("MOON_PHASE_NEW:\n  kronos: 1.0\n")
    with pytest.raises(WeightsError, match="unknown regime"):
        load_weights(bad)


def test_load_unknown_source_rejected(tmp_path):
    bad = tmp_path / "w.yaml"
    minimal_regimes = "\n".join(
        f"{r.value}:\n  not_a_real_source: 1.0" for r in Regime
    )
    bad.write_text(minimal_regimes)
    with pytest.raises(WeightsError, match="unknown source"):
        load_weights(bad)


def _full_weights_dict(override: dict | None = None) -> dict:
    """Build a complete regimes dict (every Regime present) for YAML tests."""
    base = {r.value: {"smart_money": 1.0} for r in Regime}
    if override:
        base.update(override)
    return base


def test_load_negative_weight_rejected(tmp_path):
    import yaml as _y
    bad = tmp_path / "w.yaml"
    bad.write_text(_y.safe_dump(_full_weights_dict({
        Regime.BULL_TRENDING.value: {"smart_money": -0.1},
    })))
    with pytest.raises(WeightsError, match="< 0"):
        load_weights(bad)


def test_load_zero_sum_rejected(tmp_path):
    """Each regime must have at least one positive weight."""
    import yaml as _y
    bad = tmp_path / "w.yaml"
    bad.write_text(_y.safe_dump(_full_weights_dict({
        Regime.BULL_TRENDING.value: {"smart_money": 0},
    })))
    with pytest.raises(WeightsError, match="weight sum is zero"):
        load_weights(bad)


def test_load_missing_regime_rejected(tmp_path):
    """Skipping any regime → load fails (UNKNOWN included)."""
    bad = tmp_path / "w.yaml"
    bad.write_text(f"{Regime.BULL_TRENDING.value}:\n  kronos: 1.0\n")
    with pytest.raises(WeightsError, match="missing regimes"):
        load_weights(bad)


# ================================================================== #
# fuser — happy paths
# ================================================================== #
def test_empty_signals_returns_neutral_zero(fuser):
    fused = fuser.fuse([], Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.NEUTRAL
    assert fused.ensemble_score == 0.0
    assert fused.sources_count == 0


def test_single_long_signal_drives_long(fuser):
    s = [make_signal(direction=Direction.LONG, strength=1.0)]
    fused = fuser.fuse(s, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.LONG
    assert fused.ensemble_score > 0
    assert fused.sources_count == 1
    assert fused.regime == "BULL_TRENDING"


def test_two_agree_on_long(fuser):
    sigs = [
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=0.8),
        make_signal(source=SignalSource.KRONOS, direction=Direction.LONG, strength=0.9),
    ]
    fused = fuser.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.LONG
    assert fused.sources_count == 2
    # Both contributions present
    assert "smart_money" in fused.contributions
    assert "kronos" in fused.contributions


def test_disagreement_picks_higher_score(fuser):
    sigs = [
        # Smart money long with high strength
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=1.0),
        # Macro short with moderate
        make_signal(source=SignalSource.MACRO, direction=Direction.SHORT, strength=0.4),
    ]
    fused = fuser.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.LONG


# ================================================================== #
# Conflict detection
# ================================================================== #
def test_conflict_when_runner_up_close():
    """Even-weight equal-strength sources on opposite directions → conflict."""
    # Use a custom uniform weights matrix to make scoring deterministic
    custom = {r.value: {s.value: 0.2 for s in SignalSource} for r in Regime}
    f = SignalFuser(custom)
    sigs = [
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=1.0),
        make_signal(source=SignalSource.KRONOS, direction=Direction.SHORT, strength=1.0),
    ]
    fused = f.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.conflict is True
    # ensemble_score is halved when conflict
    # Without conflict it'd be 0.2 / 0.4 = 0.5; with halving = 0.25
    assert fused.ensemble_score == pytest.approx(0.25, abs=0.01)


def test_no_conflict_when_runner_up_dominated():
    custom = {r.value: {s.value: 0.2 for s in SignalSource} for r in Regime}
    f = SignalFuser(custom)
    sigs = [
        # Three long signals, one weak short
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=0.9),
        make_signal(source=SignalSource.KRONOS, direction=Direction.LONG, strength=0.9),
        make_signal(source=SignalSource.TA, direction=Direction.LONG, strength=0.9),
        make_signal(source=SignalSource.MACRO, direction=Direction.SHORT, strength=0.3),
    ]
    fused = f.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.conflict is False


# ================================================================== #
# Staleness
# ================================================================== #
def test_stale_signal_downweighted(fuser):
    """A signal past expires_at gets a multiplier (default 0.3)."""
    base = datetime.now(timezone.utc)
    fresh = make_signal(strength=1.0, ts=base, expires_in_min=60)
    stale = make_signal(
        source=SignalSource.MACRO, direction=Direction.SHORT,
        strength=1.0, ts=base - timedelta(hours=3), expires_in_min=60,
    )
    fused = fuser.fuse([fresh, stale], Regime.BULL_TRENDING,
                       symbol="X", horizon="1h", now=base)
    # Fresh long should win cleanly despite stale short with same raw strength
    assert fused.direction == Direction.LONG


def test_staleness_factor_zero_drops_stale_completely():
    custom = {r.value: {s.value: 0.2 for s in SignalSource} for r in Regime}
    f = SignalFuser(custom, config=FuserConfig(staleness_factor=0.0))
    base = datetime.now(timezone.utc)
    stale = make_signal(strength=1.0, ts=base - timedelta(hours=2), expires_in_min=60)
    fused = f.fuse([stale], Regime.BULL_TRENDING, symbol="X", horizon="1h", now=base)
    # Sole signal got zeroed → ensemble_score 0
    assert fused.ensemble_score == 0.0


# ================================================================== #
# Source filtering
# ================================================================== #
def test_unweighted_source_ignored(tmp_path):
    """If a regime row doesn't list a source, signals from that source
    are ignored (treated as weight=0)."""
    import yaml as _y
    minimal = tmp_path / "w.yaml"
    minimal.write_text(_y.safe_dump(
        {r.value: {"smart_money": 0.5, "kronos": 0.5} for r in Regime}
    ))
    weights = load_weights(minimal)
    f = SignalFuser(weights)

    sigs = [
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=0.8),
        # AI_LLM unlisted → ignored
        make_signal(source=SignalSource.AI_LLM, direction=Direction.SHORT, strength=1.0),
    ]
    fused = f.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.LONG
    # sources_count counts weighted sources actually used (1, not 2)
    assert fused.sources_count == 1
    assert "ai_llm" not in fused.contributions


def test_no_weighted_sources_returns_neutral(fuser):
    """If every signal is for an unweighted source under this regime →
    neutral with zero score (treat like empty input)."""
    custom = {r.value: {SignalSource.MACRO.value: 1.0} for r in Regime}
    f = SignalFuser(custom)
    sigs = [
        make_signal(source=SignalSource.SMART_MONEY, direction=Direction.LONG, strength=1.0),
    ]
    fused = f.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h")
    assert fused.direction == Direction.NEUTRAL
    assert fused.sources_count == 0


# ================================================================== #
# get_weights_for — fallback to UNKNOWN
# ================================================================== #
def test_get_weights_for_known_regime(weights):
    from fusion.weights import get_weights_for
    bull = get_weights_for(Regime.BULL_TRENDING, weights)
    assert "kronos" in bull


def test_get_weights_for_unknown_falls_back(weights):
    from fusion.weights import get_weights_for
    # If a Regime were absent from yaml, get_weights_for falls back to UNKNOWN
    # (load_weights enforces presence so this scenario only happens with
    # programmatic dicts, but worth covering).
    partial = {"UNKNOWN": weights["UNKNOWN"]}
    fallback = get_weights_for(Regime.BULL_TRENDING, partial)
    assert fallback == weights["UNKNOWN"]


# ================================================================== #
# Determinism
# ================================================================== #
def test_fuse_is_deterministic(fuser):
    base_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    sigs = [
        make_signal(source=SignalSource.SMART_MONEY, ts=base_ts, strength=0.8),
        make_signal(source=SignalSource.KRONOS, ts=base_ts, strength=0.9),
    ]
    a = fuser.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h", now=base_ts)
    b = fuser.fuse(sigs, Regime.BULL_TRENDING, symbol="X", horizon="1h", now=base_ts)
    assert a.direction == b.direction
    assert a.ensemble_score == b.ensemble_score
    assert a.contributions == b.contributions
