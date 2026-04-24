"""Tests for smart_money.scanner.realtime (P4a WS listener).

The HL SDK is threading-based, not asyncio, so we mock the `Info` instance
with a FakeInfo that lets tests drive callbacks directly without opening
a real socket.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

import pytest

from smart_money.scanner.realtime import HLFillsListener
from smart_money.signals.types import RawFillEvent


# ------------------------------------------------------------------ #
# FakeInfo — records subscribe calls and lets tests fire callbacks
# ------------------------------------------------------------------ #
class FakeInfo:
    """Mimics hyperliquid.info.Info.subscribe/disconnect_websocket for tests."""

    def __init__(self, api_url: str = ""):
        self.api_url = api_url
        self.subscriptions: list[tuple[dict, Callable]] = []
        self.disconnected = False
        self._next_id = 0

    def subscribe(self, subscription: dict, callback: Callable) -> int:
        self.subscriptions.append((subscription, callback))
        self._next_id += 1
        return self._next_id

    def disconnect_websocket(self) -> None:
        self.disconnected = True

    def fire(self, address: str, msg: dict) -> None:
        """Simulate HL pushing a message for the given address."""
        for sub, cb in self.subscriptions:
            if sub.get("user", "").lower() == address.lower():
                cb(msg)


def make_userfills_msg(
    *,
    user: str,
    fills: list[dict],
    is_snapshot: bool = False,
) -> dict:
    return {
        "channel": "userFills",
        "data": {"user": user, "fills": fills, "isSnapshot": is_snapshot},
    }


def make_fill(tid: int = 1, time_ms: int = 1_700_000_000_000) -> dict:
    return {
        "tid": tid,
        "coin": "BTC",
        "px": "50000",
        "sz": "0.5",
        "side": "B",
        "time": time_ms,
        "dir": "Open Long",
        "fee": "1.0",
    }


# ------------------------------------------------------------------ #
# Fixtures
# ------------------------------------------------------------------ #
@pytest.fixture
def queue() -> asyncio.Queue[RawFillEvent]:
    return asyncio.Queue()


@pytest.fixture
def fake_info() -> FakeInfo:
    return FakeInfo()


# ------------------------------------------------------------------ #
# Subscription behaviour
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_start_subscribes_to_each_address(queue, fake_info):
    listener = HLFillsListener(
        ["0xAbC", "0xDeF"],
        queue,
        asyncio.get_running_loop(),
        info_factory=lambda _: fake_info,
    )
    await listener.start()

    # Two addresses → two subscriptions
    assert len(fake_info.subscriptions) == 2
    users = [sub["user"] for sub, _ in fake_info.subscriptions]
    # Addresses are lowercased at construction
    assert users == ["0xabc", "0xdef"]

    # Both are userFills subscriptions
    types = [sub["type"] for sub, _ in fake_info.subscriptions]
    assert types == ["userFills", "userFills"]

    await listener.stop()


@pytest.mark.asyncio
async def test_snapshot_message_is_skipped(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()

    # First message is an explicit snapshot
    fake_info.fire(
        "0xabc",
        make_userfills_msg(user="0xabc", fills=[make_fill(1), make_fill(2)], is_snapshot=True),
    )

    # Give the event loop a chance to process
    await asyncio.sleep(0.05)
    assert queue.empty(), "snapshot fills should be dropped"

    await listener.stop()


@pytest.mark.asyncio
async def test_delta_message_enqueues_events(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()

    # Mark snapshot seen so follow-up small msg isn't re-interpreted as one
    listener._snapshot_seen.add("0xabc")

    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[make_fill(42)]))

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.hl_trade_id == 42
    assert event.source == "ws"
    assert event.wallet_address == "0xabc"

    await listener.stop()


@pytest.mark.asyncio
async def test_large_first_message_without_flag_treated_as_snapshot(queue, fake_info):
    """Some HL deployments send the backfill without an explicit flag; if we
    haven't seen a snapshot yet and msg has many fills, skip it."""
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()

    # 11 fills, no isSnapshot flag — should still be skipped
    big_msg = make_userfills_msg(
        user="0xabc",
        fills=[make_fill(i) for i in range(11)],
        is_snapshot=False,
    )
    fake_info.fire("0xabc", big_msg)
    await asyncio.sleep(0.05)
    assert queue.empty()

    # Subsequent small delta should now go through
    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[make_fill(999)]))
    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.hl_trade_id == 999

    await listener.stop()


# ------------------------------------------------------------------ #
# Callback resilience
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_malformed_msg_does_not_crash_listener(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()
    listener._snapshot_seen.add("0xabc")

    # Fire garbage
    fake_info.fire("0xabc", {"channel": "userFills"})  # no data
    fake_info.fire("0xabc", {"channel": "userFills", "data": "not-a-dict"})

    # Then a good one
    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[make_fill(7)]))

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.hl_trade_id == 7

    await listener.stop()


@pytest.mark.asyncio
async def test_malformed_fill_among_good_fills_only_skips_bad(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()
    listener._snapshot_seen.add("0xabc")

    bad = {"tid": 99}  # missing almost everything
    good = make_fill(100)
    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[bad, good]))

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert event.hl_trade_id == 100
    # No second event
    assert queue.empty()

    await listener.stop()


# ------------------------------------------------------------------ #
# on_dispatch hook (used to share seen_tids with reconciler)
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_on_dispatch_hook_fires_after_enqueue(queue, fake_info):
    seen_tids: set[int] = set()

    def _mark(ev: RawFillEvent) -> None:
        seen_tids.add(ev.hl_trade_id)

    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(),
        info_factory=lambda _: fake_info,
        on_dispatch=_mark,
    )
    await listener.start()
    listener._snapshot_seen.add("0xabc")

    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[make_fill(55)]))
    await asyncio.wait_for(queue.get(), timeout=1.0)

    assert seen_tids == {55}
    await listener.stop()


# ------------------------------------------------------------------ #
# Lifecycle: stop disconnects
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_stop_disconnects_websocket(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()
    await listener.stop()

    assert fake_info.disconnected is True


# ------------------------------------------------------------------ #
# Latency timestamps captured in WS callback thread
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_ws_received_timestamp_set_by_callback(queue, fake_info):
    listener = HLFillsListener(
        ["0xabc"], queue, asyncio.get_running_loop(), info_factory=lambda _: fake_info,
    )
    await listener.start()
    listener._snapshot_seen.add("0xabc")

    # Fill's HL time is way in the past; ts_ws_received should be "now" (large)
    old_fill = make_fill(1, time_ms=1_600_000_000_000)  # 2020-ish
    fake_info.fire("0xabc", make_userfills_msg(user="0xabc", fills=[old_fill]))

    event = await asyncio.wait_for(queue.get(), timeout=1.0)
    # Network latency should be large (years between 2020 and now)
    assert event.network_latency_ms > 10_000_000  # >10M ms = ~3h+, easily met

    await listener.stop()
