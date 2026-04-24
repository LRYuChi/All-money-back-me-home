"""REST fill reconciler — safety net for the WebSocket listener.

Polls HL `userFillsByTime` every `interval_sec` for the last
`lookback_sec` window, de-dupes against fills already seen by the WS
listener, and emits any stragglers on the same event queue.

Targets the P4a Gate: reconciler-recovered fills < 0.1% of total.

Not the primary feed — WebSocket is. This exists solely to mask:
  - WS reconnect gaps (seconds of downtime)
  - dropped/missed `userFills` messages
  - HL transient ws outages

Memory discipline:
    `_seen_tids` is bounded: on each pass we drop tids older than
    `lookback_sec * 2`. Running for a year would otherwise accumulate
    a seen-set of millions of ints.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Protocol

from smart_money.signals.dispatcher import build_raw_event, now_ms
from smart_money.signals.types import RawFillEvent

logger = logging.getLogger(__name__)


class UserFillsByTimeClient(Protocol):
    """Minimal interface reconciler needs — matches hyperliquid.info.Info."""

    def user_fills_by_time(
        self,
        address: str,
        start_time: int,
        end_time: int | None = None,
        aggregate_by_time: bool | None = False,
    ) -> list[dict[str, Any]]: ...


class FillsReconciler:
    """Background task: every `interval_sec`, REST-poll each address's recent fills
    and enqueue any not already seen via WS.

    Use with:
        reconciler = FillsReconciler(addresses, info, queue)
        await reconciler.run()               # blocks until cancelled

    Or run as a task:
        task = asyncio.create_task(reconciler.run())
        task.cancel()                        # clean shutdown
    """

    def __init__(
        self,
        addresses: list[str],
        info: UserFillsByTimeClient,
        event_queue: asyncio.Queue[RawFillEvent],
        *,
        interval_sec: int = 60,
        lookback_sec: int = 300,
        seen_tids: set[int] | None = None,
    ) -> None:
        self._addresses = [a.lower() for a in addresses]
        self._info = info
        self._event_queue = event_queue
        self._interval_sec = interval_sec
        self._lookback_sec = lookback_sec
        # seen_tids is injectable for cross-process dedup (e.g. shared with WS listener).
        # If None, reconciler owns its own set.
        self._seen_tids: set[int] = seen_tids if seen_tids is not None else set()
        # Track fill ts alongside tid so we can expire old entries:
        self._seen_tid_ts_ms: dict[int, int] = {}

    async def run(self) -> None:
        """Poll loop. Cancel externally to stop."""
        logger.info(
            "reconciler starting: %d addresses, interval=%ds, lookback=%ds",
            len(self._addresses), self._interval_sec, self._lookback_sec,
        )
        while True:
            await self._run_once()
            await asyncio.sleep(self._interval_sec)

    async def _run_once(self) -> None:
        """One sweep across all addresses. Swallows per-address errors so one
        bad wallet doesn't stop the sweep for the rest."""
        t0 = now_ms()
        end = t0
        start = t0 - self._lookback_sec * 1000
        recovered = 0

        for addr in self._addresses:
            try:
                fills = await asyncio.to_thread(
                    self._info.user_fills_by_time, addr, start, end, False
                )
            except Exception as e:
                logger.warning("reconciler: user_fills_by_time failed for %s: %s", addr[:10], e)
                continue

            for fill in fills:
                try:
                    tid = int(fill["tid"])
                except (KeyError, ValueError, TypeError):
                    continue
                if tid in self._seen_tids:
                    continue

                try:
                    event = build_raw_event(fill, addr, source="reconciler")
                except Exception as e:
                    logger.warning("reconciler: build_raw_event failed for tid=%s: %s", tid, e)
                    continue

                await self._event_queue.put(event)
                self._seen_tids.add(tid)
                # Record *when we saw it*, not when the fill happened — otherwise
                # ancient fills (e.g. in tests) would prune immediately, causing
                # re-emission on the next sweep.
                self._seen_tid_ts_ms[tid] = t0
                recovered += 1

        self._prune_seen(cutoff_ms=t0 - self._lookback_sec * 2 * 1000)
        logger.debug(
            "reconciler sweep: %dms, recovered=%d, seen_cache=%d",
            now_ms() - t0, recovered, len(self._seen_tids),
        )

    def _prune_seen(self, cutoff_ms: int) -> None:
        """Drop tids older than cutoff from memory."""
        stale = [tid for tid, ts in self._seen_tid_ts_ms.items() if ts < cutoff_ms]
        for tid in stale:
            self._seen_tids.discard(tid)
            self._seen_tid_ts_ms.pop(tid, None)

    def mark_seen(self, tid: int, ts_hl_fill_ms: int | None = None) -> None:
        """Called by WS listener after each successful dispatch to dedup REST path.

        The `ts_hl_fill_ms` argument is accepted for API clarity but ignored —
        we always stamp the seen time with `now_ms()`, since prune is measured
        against "how long since we last saw this tid", not fill age.
        """
        del ts_hl_fill_ms  # intentionally unused
        self._seen_tids.add(tid)
        self._seen_tid_ts_ms[tid] = now_ms()


__all__ = ["FillsReconciler", "UserFillsByTimeClient"]
