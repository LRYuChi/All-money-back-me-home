"""Tests for smart_money.scanner.reconciler (P4a)."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from smart_money.scanner.reconciler import FillsReconciler
from smart_money.signals.types import RawFillEvent


# ------------------------------------------------------------------ #
# Fake HL client
# ------------------------------------------------------------------ #
class FakeInfo:
    """Minimal user_fills_by_time contract with per-address scripted fills
    and optional error injection."""

    def __init__(self, fills_by_address: dict[str, list[dict]] | None = None):
        self._fills = fills_by_address or {}
        self.raises: dict[str, Exception] = {}
        self.call_count = 0

    def user_fills_by_time(
        self,
        address: str,
        start_time: int,
        end_time: int | None = None,
        aggregate_by_time: bool | None = False,
    ) -> list[dict[str, Any]]:
        self.call_count += 1
        if address in self.raises:
            raise self.raises[address]
        return list(self._fills.get(address.lower(), []))


def make_fill(tid: int, ts_ms: int = 1_700_000_000_000, coin: str = "BTC") -> dict:
    return {
        "tid": tid,
        "coin": coin,
        "px": "50000",
        "sz": "0.1",
        "side": "B",
        "time": ts_ms,
        "dir": "Open Long",
        "fee": "1.0",
    }


@pytest.fixture
def queue() -> asyncio.Queue[RawFillEvent]:
    return asyncio.Queue()


# ------------------------------------------------------------------ #
# _run_once: single sweep behaviour
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_first_sweep_emits_all_fills(queue):
    fake = FakeInfo({"0xabc": [make_fill(1), make_fill(2), make_fill(3)]})
    reconciler = FillsReconciler(["0xabc"], fake, queue, lookback_sec=300)

    await reconciler._run_once()

    # All three fills should land on the queue
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 3
    assert {e.hl_trade_id for e in events} == {1, 2, 3}


@pytest.mark.asyncio
async def test_second_sweep_skips_already_seen(queue):
    fake = FakeInfo({"0xabc": [make_fill(1), make_fill(2)]})
    reconciler = FillsReconciler(["0xabc"], fake, queue)

    await reconciler._run_once()
    # Second sweep with the same fills still present — should emit zero
    await reconciler._run_once()

    count = 0
    while not queue.empty():
        queue.get_nowait()
        count += 1
    assert count == 2  # only the first sweep's two


@pytest.mark.asyncio
async def test_dedup_via_external_seen_set_from_ws(queue):
    """Reconciler honours externally-managed seen set (shared with WS)."""
    seen: set[int] = {1, 2}  # WS already captured tids 1 and 2
    fake = FakeInfo({"0xabc": [make_fill(1), make_fill(2), make_fill(3)]})
    reconciler = FillsReconciler(["0xabc"], fake, queue, seen_tids=seen)

    await reconciler._run_once()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 1
    assert events[0].hl_trade_id == 3  # only the one WS missed


@pytest.mark.asyncio
async def test_error_on_one_address_does_not_stop_others(queue):
    fake = FakeInfo({
        "0xabc": [make_fill(1)],
        "0xdef": [make_fill(2)],
    })
    fake.raises["0xabc"] = RuntimeError("HL 500")

    reconciler = FillsReconciler(["0xabc", "0xdef"], fake, queue)
    await reconciler._run_once()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 1
    assert events[0].hl_trade_id == 2


@pytest.mark.asyncio
async def test_malformed_fill_skipped_without_crash(queue):
    bad_fill = {"tid": 99}  # missing nearly everything
    good_fill = make_fill(100)
    fake = FakeInfo({"0xabc": [bad_fill, good_fill]})

    reconciler = FillsReconciler(["0xabc"], fake, queue)
    await reconciler._run_once()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert len(events) == 1
    assert events[0].hl_trade_id == 100


# ------------------------------------------------------------------ #
# Memory discipline: _prune_seen drops old tids
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_prune_seen_drops_old_tids(queue):
    reconciler = FillsReconciler(["0xabc"], FakeInfo(), queue, lookback_sec=60)
    # Manually plant old tids
    reconciler._seen_tids = {1, 2, 3}
    reconciler._seen_tid_ts_ms = {
        1: 1_700_000_000_000,  # ancient
        2: 1_700_000_000_000,  # ancient
        3: 9_999_999_999_999,  # future
    }
    # Cutoff = only keep tids with ts >= 5e12
    reconciler._prune_seen(cutoff_ms=5_000_000_000_000)

    assert reconciler._seen_tids == {3}
    assert list(reconciler._seen_tid_ts_ms.keys()) == [3]


# ------------------------------------------------------------------ #
# mark_seen: called by WS listener to skip REST dedup
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_mark_seen_prevents_rest_reemission(queue):
    fake = FakeInfo({"0xabc": [make_fill(42)]})
    reconciler = FillsReconciler(["0xabc"], fake, queue)

    # WS processed tid 42 first, marks it seen
    reconciler.mark_seen(42, ts_hl_fill_ms=1_700_000_000_000)
    await reconciler._run_once()

    assert queue.empty()


# ------------------------------------------------------------------ #
# Source tagging on emitted events
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_emitted_events_tagged_as_reconciler_source(queue):
    fake = FakeInfo({"0xabc": [make_fill(1)]})
    reconciler = FillsReconciler(["0xabc"], fake, queue)
    await reconciler._run_once()

    event = queue.get_nowait()
    assert event.source == "reconciler"


# ------------------------------------------------------------------ #
# Address lowercasing — HL addresses are case-insensitive
# ------------------------------------------------------------------ #
@pytest.mark.asyncio
async def test_addresses_are_lowercased_on_construction(queue):
    fake = FakeInfo({"0xabc": [make_fill(1)]})
    # Pass uppercase; reconciler should lowercase before calling user_fills_by_time
    reconciler = FillsReconciler(["0xABC"], fake, queue)
    await reconciler._run_once()

    # Fill should have been found (FakeInfo.user_fills_by_time lowercases lookup)
    event = queue.get_nowait()
    assert event.wallet_address == "0xabc"
