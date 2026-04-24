"""Build BacktestSnapshot from various sources.

Helpers cover:
  - `current_git_commit()` — short SHA + dirty-flag detection
  - `build_snapshot()` — generic constructor with sensible defaults
  - per-kind builders to land in Phase B+/C as we wire each backtest:
      * `from_p3_gate(report, config)` — smart_money/backtest validator
      * `from_strategy_eval(...)` — Phase E
      * `from_kronos_finetune(...)` — Phase C

For now we ship the generic builder + git helper. Per-kind builders
will be added in the rounds that wire each backtest.
"""
from __future__ import annotations

import logging
import subprocess
from typing import Any

from shared.snapshots.types import BacktestSnapshot

logger = logging.getLogger(__name__)


def current_git_commit() -> tuple[str | None, bool | None]:
    """Return (short_sha, dirty) for the current repo.

    Returns (None, None) outside a git repo or if git command unavailable —
    snapshot continues, just without provenance.
    """
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        if sha.returncode != 0:
            return None, None
        short = sha.stdout.strip() or None
        if not short:
            return None, None

        # Detect dirty state
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=3,
        )
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return short, dirty
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None, None


def build_snapshot(
    *,
    kind: str,
    config: dict[str, Any],
    report: dict[str, Any],
    cutoffs: list[str] | None = None,
    data_window: dict[str, str] | None = None,
    rng_seed: int | None = None,
    decision_pass: bool | None = None,
    decision_reason: str | None = None,
    n_trades: int | None = None,
    median_pnl_pct: float | None = None,
    max_drawdown: float | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
) -> BacktestSnapshot:
    """Construct a BacktestSnapshot, auto-filling git provenance if not supplied."""
    if git_commit is None and git_dirty is None:
        git_commit, git_dirty = current_git_commit()

    return BacktestSnapshot(
        kind=kind,
        config=config,
        report=report,
        cutoffs=cutoffs,
        data_window=data_window,
        rng_seed=rng_seed,
        decision_pass=decision_pass,
        decision_reason=decision_reason,
        n_trades=n_trades,
        median_pnl_pct=median_pnl_pct,
        max_drawdown=max_drawdown,
        git_commit=git_commit,
        git_dirty=git_dirty,
    )


__all__ = ["current_git_commit", "build_snapshot"]
