"""Tests for the /healthz HTTP server (round 39)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from execution.pending_orders import (
    InMemoryPendingOrderQueue,
    PendingOrder,
    PendingOrderStatus,
    SweepStats,
    serve_healthz,
)


# ================================================================== #
# Test infra: spawn server on ephemeral port, return base URL
# ================================================================== #
async def _spawn_server(
    *,
    worker_stats=lambda: {"claimed": 0, "filled": 0},
    queue_provider=None,
    sweep_stats=None,
):
    if queue_provider is None:
        q = InMemoryPendingOrderQueue()
        queue_provider = lambda: q
    server = await serve_healthz(
        port=0,                    # ephemeral
        host="127.0.0.1",
        worker_stats=worker_stats,
        queue_provider=queue_provider,
        sweep_stats=sweep_stats,
    )
    socks = server.sockets or []
    assert socks, "server bound no sockets"
    port = socks[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


async def _close(server: asyncio.Server) -> None:
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=2.0)
    except asyncio.TimeoutError:
        pass


# ================================================================== #
# /healthz — always 200
# ================================================================== #
@pytest.mark.asyncio
async def test_healthz_returns_ok():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/healthz", timeout=2.0)
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["uptime_sec"] >= 0
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_healthz_uptime_grows():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r1 = (await client.get(f"{base}/healthz", timeout=2.0)).json()
            await asyncio.sleep(0.05)
            r2 = (await client.get(f"{base}/healthz", timeout=2.0)).json()
        assert r2["uptime_sec"] > r1["uptime_sec"]
    finally:
        await _close(server)


# ================================================================== #
# /ready — 200 when queue OK, 503 when queue raises
# ================================================================== #
@pytest.mark.asyncio
async def test_ready_returns_200_with_healthy_queue():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/ready", timeout=2.0)
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["queue_ok"] is True
        assert body["depth"] == 0   # empty InMemory queue
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_ready_reflects_queue_depth():
    q = InMemoryPendingOrderQueue()
    for _ in range(3):
        q.enqueue(PendingOrder(
            strategy_id="s", symbol="X", side="long",
            target_notional_usd=100, mode="shadow",
        ))
    server, base = await _spawn_server(queue_provider=lambda: q)
    try:
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"{base}/ready", timeout=2.0)).json()
        assert body["depth"] == 3
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_ready_returns_503_when_queue_explodes():
    class BoomQueue:
        def list_recent(self, **_):
            raise ConnectionError("DB down")
    server, base = await _spawn_server(queue_provider=lambda: BoomQueue())
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/ready", timeout=2.0)
        assert r.status_code == 503
        body = r.json()
        assert body["ready"] is False
        assert body["depth"] == -1
    finally:
        await _close(server)


# ================================================================== #
# /stats — full snapshot
# ================================================================== #
@pytest.mark.asyncio
async def test_stats_includes_worker_dict():
    stats = {"claimed": 5, "filled": 3, "rejected": 2, "guard_denies": 1}
    server, base = await _spawn_server(worker_stats=lambda: stats)
    try:
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"{base}/stats", timeout=2.0)).json()
        assert body["worker"] == stats
        assert body["queue_depth"] == 0
        assert body["uptime_sec"] >= 0
        assert body["sweep"] is None   # no sweep_stats provided
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_stats_serialises_sweep_dataclass():
    sweep = SweepStats(iterations=4, total_expired=2, errors=0)
    server, base = await _spawn_server(sweep_stats=lambda: sweep)
    try:
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"{base}/stats", timeout=2.0)).json()
        assert body["sweep"] == {
            "iterations": 4, "total_expired": 2, "errors": 0,
        }
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_stats_handles_sweep_provider_returning_none():
    """Sweeper not yet completed → provider returns None → render as {}."""
    server, base = await _spawn_server(sweep_stats=lambda: None)
    try:
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"{base}/stats", timeout=2.0)).json()
        assert body["sweep"] == {}
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_stats_swallows_worker_stats_exception():
    def bad_stats():
        raise RuntimeError("worker stats broken")
    server, base = await _spawn_server(worker_stats=bad_stats)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/stats", timeout=2.0)
        # Still 200 — endpoint returns the error in the JSON body
        assert r.status_code == 200
        body = r.json()
        assert "error" in body["worker"]
    finally:
        await _close(server)


# ================================================================== #
# Unknown path → 404
# ================================================================== #
@pytest.mark.asyncio
async def test_unknown_path_returns_404():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/nope", timeout=2.0)
        assert r.status_code == 404
        assert "unknown" in r.json()["error"]
    finally:
        await _close(server)


# ================================================================== #
# Method handling
# ================================================================== #
@pytest.mark.asyncio
async def test_post_returns_405():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{base}/healthz", timeout=2.0)
        assert r.status_code == 405
    finally:
        await _close(server)


# ================================================================== #
# Query string tolerated (e.g. /healthz?from=k8s)
# ================================================================== #
@pytest.mark.asyncio
async def test_path_with_query_string_routes_correctly():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/healthz?from=k8s-probe", timeout=2.0)
        assert r.status_code == 200
        assert r.json()["ok"] is True
    finally:
        await _close(server)


# ================================================================== #
# Server can be closed cleanly + new one bound on different port
# ================================================================== #
@pytest.mark.asyncio
async def test_server_closes_cleanly():
    server, base = await _spawn_server()
    await _close(server)

    # After closing, the next connect should fail
    async with httpx.AsyncClient() as client:
        with pytest.raises(httpx.ConnectError):
            await client.get(f"{base}/healthz", timeout=0.5)


# ================================================================== #
# Concurrent requests
# ================================================================== #
@pytest.mark.asyncio
async def test_handles_concurrent_requests():
    server, base = await _spawn_server()
    try:
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*[
                client.get(f"{base}/healthz", timeout=2.0) for _ in range(10)
            ])
        assert all(r.status_code == 200 for r in results)
    finally:
        await _close(server)


# ================================================================== #
# End-to-end: real queue with mixed PENDING + non-PENDING
# ================================================================== #
@pytest.mark.asyncio
async def test_e2e_depth_counts_only_pending():
    q = InMemoryPendingOrderQueue()
    o1 = PendingOrder(strategy_id="s", symbol="X", side="long",
                      target_notional_usd=100, mode="shadow")
    o2 = PendingOrder(strategy_id="s", symbol="X", side="long",
                      target_notional_usd=100, mode="shadow")
    q.enqueue(o1)
    oid2 = q.enqueue(o2)
    q.update_status(oid2, PendingOrderStatus.FILLED)

    server, base = await _spawn_server(queue_provider=lambda: q)
    try:
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"{base}/ready", timeout=2.0)).json()
        assert body["depth"] == 1   # only the still-PENDING one
    finally:
        await _close(server)
