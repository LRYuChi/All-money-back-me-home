"""Default guard pipeline factory — singleton with disk persistence.

The pipeline is a singleton so stateful guards retain tracking data.
State is persisted to JSON so it survives bot restarts.
"""

import json
import logging
import os
from pathlib import Path

from guards.base import GuardPipeline
from guards.guards import (
    ConsecutiveLossGuard,
    CooldownGuard,
    DailyLossGuard,
    DrawdownGuard,
    LiquidationGuard,
    MaxLeverageGuard,
    MaxPositionGuard,
    TotalExposureGuard,
)

logger = logging.getLogger(__name__)

# Module-level singleton
_default_pipeline: GuardPipeline | None = None

# State file path
_STATE_DIR = Path(os.environ.get("DATA_DIR", "/data"))
_STATE_FILE = _STATE_DIR / "guard_state.json"


def create_default_pipeline() -> GuardPipeline:
    """Return the singleton guard pipeline (creates on first call).

    Loads persisted state from disk if available.
    """
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = GuardPipeline([
            MaxPositionGuard(max_pct=30),
            MaxLeverageGuard(max_leverage=5),
            LiquidationGuard(min_distance_mult=2.0),
            TotalExposureGuard(max_pct=80),
            DrawdownGuard(max_drawdown_pct=10),
            CooldownGuard(minutes=15),
            DailyLossGuard(max_pct=5),
            ConsecutiveLossGuard(max_streak=5, pause_hours=24),
        ])
        _load_state()
    return _default_pipeline


def get_guard(guard_type: type):
    """Get a specific guard instance from the singleton pipeline."""
    pipeline = create_default_pipeline()
    for g in pipeline.guards:
        if isinstance(g, guard_type):
            return g
    return None


def save_state() -> None:
    """Persist guard state to disk."""
    if _default_pipeline is None:
        return

    state = {}
    for g in _default_pipeline.guards:
        if isinstance(g, DailyLossGuard):
            state["daily_loss"] = g._daily_loss
            state["daily_reset_day"] = g._reset_day
        elif isinstance(g, ConsecutiveLossGuard):
            state["consec_streak"] = g._streak
            state["consec_paused_until"] = g._paused_until
        elif isinstance(g, CooldownGuard):
            state["cooldown_last_trade"] = g._last_trade
        elif isinstance(g, DrawdownGuard):
            state["drawdown_peak_equity"] = g._peak_equity

    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning("Guard state save failed: %s", e)


def _load_state() -> None:
    """Load guard state from disk."""
    if _default_pipeline is None:
        return

    try:
        if not _STATE_FILE.exists():
            return
        with open(_STATE_FILE) as f:
            state = json.load(f)
    except Exception as e:
        logger.warning("Guard state load failed: %s", e)
        return

    for g in _default_pipeline.guards:
        if isinstance(g, DailyLossGuard):
            g._daily_loss = state.get("daily_loss", 0.0)
            g._reset_day = state.get("daily_reset_day", "")
        elif isinstance(g, ConsecutiveLossGuard):
            g._streak = state.get("consec_streak", 0)
            g._paused_until = state.get("consec_paused_until", 0)
        elif isinstance(g, CooldownGuard):
            g._last_trade = state.get("cooldown_last_trade", {})
        elif isinstance(g, DrawdownGuard):
            g._peak_equity = state.get("drawdown_peak_equity", 0.0)

    logger.info("Guard state loaded: daily_loss=%.2f, streak=%d",
                state.get("daily_loss", 0), state.get("consec_streak", 0))


def get_state_summary() -> dict:
    """Get current guard state as a dict (for API/dashboard)."""
    pipeline = create_default_pipeline()
    result = {}
    for g in pipeline.guards:
        if isinstance(g, DailyLossGuard):
            result["daily_loss"] = g._daily_loss
            result["daily_loss_limit_pct"] = g.max_pct
        elif isinstance(g, ConsecutiveLossGuard):
            result["consecutive_losses"] = g._streak
            result["max_streak"] = g.max_streak
            result["paused_until"] = g._paused_until
        elif isinstance(g, CooldownGuard):
            result["cooldown_symbols"] = len(g._last_trade)
        elif isinstance(g, DrawdownGuard):
            result["drawdown_peak_equity"] = g._peak_equity
            result["drawdown_max_pct"] = g.max_drawdown_pct
    return result
