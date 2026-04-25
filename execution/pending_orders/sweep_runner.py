"""Background sweep loop helper (round 38).

Runs `queue.sweep_expired(...)` on a fixed cadence until a `stop_event`
is set. Designed to be `asyncio.create_task()`'d alongside the worker's
dispatch loop so a single CLI invocation handles both.

Why factor out a helper:
  - Testable in isolation (no dependency on the worker CLI's argparse).
  - Reusable when other daemons want a sweeper sidecar.
  - Keeps the CLI thin.

Failure handling:
  - sweep_expired() exceptions are caught + logged + counted; the loop
    continues. A single flaky DB tick must not kill the sweeper sidecar.
  - On stop_event the loop exits between sweeps (no in-flight sweep is
    interrupted; sweep_expired itself is bounded so this is fine).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SweepStats:
    """Per-loop counters; caller can inspect after stop."""
    iterations: int = 0
    total_expired: int = 0
    errors: int = 0


async def background_sweep_loop(
    queue,                                    # PendingOrderQueue
    stop_event: asyncio.Event,
    *,
    interval_sec: float,
    pending_max_age_sec: float = 0,
    dispatching_max_age_sec: float = 0,
) -> SweepStats:
    """Run sweep_expired every `interval_sec` until stop_event is set.

    Returns the final SweepStats so caller can log final counters
    (worker CLI reports them via `worker.stats()` style).

    interval_sec must be > 0; both age thresholds 0 = sweeper does
    nothing useful (caller should avoid that).
    """
    if interval_sec <= 0:
        raise ValueError(
            f"interval_sec must be > 0; got {interval_sec}"
        )
    if pending_max_age_sec <= 0 and dispatching_max_age_sec <= 0:
        logger.warning(
            "background_sweep_loop: both age thresholds are 0; sweeper "
            "will iterate but never expire anything",
        )

    stats = SweepStats()
    logger.info(
        "background sweeper starting: interval=%.1fs pending_max_age=%.0fs "
        "dispatching_max_age=%.0fs",
        interval_sec, pending_max_age_sec, dispatching_max_age_sec,
    )

    while not stop_event.is_set():
        stats.iterations += 1
        try:
            n = queue.sweep_expired(
                pending_max_age_sec=pending_max_age_sec,
                dispatching_max_age_sec=dispatching_max_age_sec,
            )
            if n > 0:
                stats.total_expired += n
                logger.info("background sweeper: expired %d order(s)", n)
        except Exception as e:
            stats.errors += 1
            logger.exception(
                "background sweeper iteration %d failed: %s",
                stats.iterations, e,
            )

        # Sleep, but wake immediately if stop_event fires
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            continue

    logger.info(
        "background sweeper stopped: iterations=%d total_expired=%d errors=%d",
        stats.iterations, stats.total_expired, stats.errors,
    )
    return stats


__all__ = ["SweepStats", "background_sweep_loop"]
