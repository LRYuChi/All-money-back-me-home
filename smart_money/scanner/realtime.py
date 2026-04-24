"""Hyperliquid WebSocket listener for userFills.

HL's python SDK (`hyperliquid.info.Info`) runs its websocket in a *thread*,
not asyncio. Callbacks fire from that thread, so we marshal events onto the
caller's asyncio queue via `run_coroutine_threadsafe`.

Reconnection strategy
---------------------
The HL SDK does NOT auto-reconnect on disconnect. We supervise the `Info`
instance externally via a silence timer: if no userFills messages have
arrived for `heartbeat_timeout_sec`, we tear down and recreate Info,
then re-subscribe all addresses. Exponential backoff capped at
`reconnect_backoff_max_sec`.

**Heartbeat caveat**: userFills is event-driven — idle whales legitimately
go hours without trading, so the timeout MUST be large. Default 30min.
Do NOT treat short silences (seconds-minutes) as liveness signals. Future
improvement (P5): subscribe to `allMids` as a keep-alive or call
`send_ping()` on the WS to verify the transport.

Snapshot handling
-----------------
First userFills message after each subscribe is a snapshot of that
wallet's historical fills. We skip these — P1 scanner already backfilled
history; the snapshot is noise for live shadowing. Subsequent messages
are deltas (new fills) and flow through normally.

Thread safety
-------------
All callbacks (_on_fills) execute in the SDK's WS thread. They only
touch `self._snapshot_seen` (a set, Python GIL protects single ops),
`self._seen_tids_shared` (external set, caller owns), and push onto the
asyncio queue via run_coroutine_threadsafe. No `await` inside callbacks.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from smart_money.signals.dispatcher import build_raw_event, now_ms
from smart_money.signals.types import RawFillEvent

logger = logging.getLogger(__name__)


# Default HL mainnet URL; can be overridden via constructor for testnet.
HL_MAINNET_API_URL = "https://api.hyperliquid.xyz"


class HLFillsListener:
    """Subscribes to userFills for a set of whitelisted wallet addresses.

    Lifecycle:
        listener = HLFillsListener(addresses, queue, loop)
        await listener.start()     # creates Info, subscribes all addresses
        # ... runs until stop()
        await listener.stop()      # unsubscribes, disconnects, joins ws thread

    Pass `info_factory` for testability — tests can inject a fake that
    mimics the Info.subscribe contract without opening a real socket.
    """

    def __init__(
        self,
        addresses: list[str],
        event_queue: asyncio.Queue[RawFillEvent],
        loop: asyncio.AbstractEventLoop,
        *,
        api_url: str = HL_MAINNET_API_URL,
        reconnect_backoff_max_sec: int = 60,
        heartbeat_timeout_sec: int = 1800,   # 30 min — whales can genuinely idle
        info_factory: Any = None,  # Callable[[str], Info]; if None, real HL SDK used
        on_dispatch: Any = None,   # Callable[[RawFillEvent], None]; called after each enqueue
    ) -> None:
        self._addresses = [a.lower() for a in addresses]
        self._event_queue = event_queue
        self._loop = loop
        self._api_url = api_url
        self._reconnect_backoff_max_sec = reconnect_backoff_max_sec
        self._heartbeat_timeout_sec = heartbeat_timeout_sec
        self._info_factory = info_factory
        self._on_dispatch = on_dispatch

        self._info: Any = None
        self._subscription_ids: dict[str, int] = {}
        # Per-address snapshot flag: first userFills msg is a full backfill; skip it.
        self._snapshot_seen: set[str] = set()
        self._last_msg_ts_ms: int = now_ms()

        self._supervisor_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    # ---------------------------------------------------------------- #
    # Public lifecycle
    # ---------------------------------------------------------------- #
    async def start(self) -> None:
        """Open connection, subscribe all addresses, start supervisor."""
        await self._connect_and_subscribe()
        self._supervisor_task = asyncio.create_task(self._supervise())

    async def stop(self) -> None:
        """Unsubscribe, disconnect, wait for supervisor to exit."""
        self._stopped.set()
        if self._supervisor_task is not None:
            self._supervisor_task.cancel()
            try:
                await self._supervisor_task
            except asyncio.CancelledError:
                pass
        await self._disconnect()

    # ---------------------------------------------------------------- #
    # Connection + subscription
    # ---------------------------------------------------------------- #
    async def _connect_and_subscribe(self) -> None:
        """Create Info instance + subscribe to each address's userFills."""
        self._info = await asyncio.to_thread(self._build_info)
        self._subscription_ids.clear()
        self._snapshot_seen.clear()

        for addr in self._addresses:
            sub = {"type": "userFills", "user": addr}
            try:
                sub_id = await asyncio.to_thread(
                    self._info.subscribe, sub, self._make_callback(addr)
                )
                self._subscription_ids[addr] = sub_id
            except Exception as e:
                logger.error("failed to subscribe %s: %s", addr[:10], e)
                continue

        self._last_msg_ts_ms = now_ms()
        logger.info(
            "WS listener: subscribed to %d/%d addresses",
            len(self._subscription_ids), len(self._addresses),
        )

    def _build_info(self) -> Any:
        """Build a fresh Info instance. Overridable via info_factory for tests."""
        if self._info_factory is not None:
            return self._info_factory(self._api_url)
        # Lazy import so tests that pass a factory don't need the HL SDK installed.
        from hyperliquid.info import Info
        return Info(base_url=self._api_url, skip_ws=False)

    async def _disconnect(self) -> None:
        if self._info is None:
            return
        try:
            await asyncio.to_thread(self._info.disconnect_websocket)
        except Exception as e:
            logger.warning("disconnect_websocket raised: %s", e)
        self._info = None

    # ---------------------------------------------------------------- #
    # WS callback — runs in SDK's worker thread, NOT the asyncio loop
    # ---------------------------------------------------------------- #
    def _make_callback(self, address: str):
        """Return a closure that dispatches fills for this specific address.

        HL emits `{"channel": "userFills", "data": {"user": addr, "fills": [...], "isSnapshot": bool}}`.
        """
        def _cb(msg: dict[str, Any]) -> None:
            ts_ws = now_ms()
            self._last_msg_ts_ms = ts_ws

            try:
                data = msg.get("data", {})
                is_snapshot = bool(data.get("isSnapshot", False))
                fills = data.get("fills", [])
            except (AttributeError, TypeError) as e:
                logger.warning("WS %s: malformed msg: %s", address[:10], e)
                return

            if is_snapshot:
                # First message after subscribe — full history. Skip: P1 scanner owns historical.
                self._snapshot_seen.add(address)
                logger.debug("WS %s: snapshot received (%d fills) — skipped", address[:10], len(fills))
                return

            # Some HL deployments emit snapshot first even without the flag; if we haven't
            # seen a snapshot yet AND there are many fills at once, treat as snapshot.
            if address not in self._snapshot_seen and len(fills) > 10:
                self._snapshot_seen.add(address)
                logger.debug(
                    "WS %s: implicit snapshot (%d fills, no flag) — skipped", address[:10], len(fills)
                )
                return

            for fill in fills:
                try:
                    event = build_raw_event(fill, address, source="ws", ts_ws_received_ms=ts_ws)
                except Exception as e:
                    logger.warning("WS %s: dispatch failed: %s", address[:10], e)
                    continue

                # Cross-thread push onto asyncio queue.
                asyncio.run_coroutine_threadsafe(self._event_queue.put(event), self._loop)
                if self._on_dispatch is not None:
                    try:
                        self._on_dispatch(event)
                    except Exception as e:
                        logger.warning("on_dispatch hook raised: %s", e)

        return _cb

    # ---------------------------------------------------------------- #
    # Supervisor — detects stalls and reconnects
    # ---------------------------------------------------------------- #
    async def _supervise(self) -> None:
        """Check heartbeat; on detected stall, rebuild Info and resubscribe."""
        backoff_sec = 1
        while not self._stopped.is_set():
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                break

            silence_sec = (now_ms() - self._last_msg_ts_ms) / 1000
            if silence_sec < self._heartbeat_timeout_sec:
                continue

            # Stall: userFills silent for >> heartbeat_timeout_sec. At 30min+
            # default this is long enough that either the WS is truly dead or
            # every whitelisted whale has gone idle — the latter is fine
            # (reconnecting is cheap; the reconciler has been covering us via
            # REST during any real gap).
            logger.warning(
                "WS stall detected: %.0fs silence. Reconnecting (backoff=%ds)",
                silence_sec, backoff_sec,
            )
            await self._disconnect()
            await asyncio.sleep(backoff_sec)

            try:
                await self._connect_and_subscribe()
                backoff_sec = 1  # reset on success
            except Exception as e:
                logger.error("reconnect failed: %s", e)
                backoff_sec = min(backoff_sec * 2, self._reconnect_backoff_max_sec)

    # ---------------------------------------------------------------- #
    # Testability helpers
    # ---------------------------------------------------------------- #
    def _force_stall(self) -> None:
        """Test hook: pretend no messages have arrived in forever, triggering reconnect."""
        self._last_msg_ts_ms = 0


__all__ = ["HLFillsListener", "HL_MAINNET_API_URL"]
