"""Tests for /metrics Prometheus endpoint (round 40)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from execution.pending_orders import (
    InMemoryPendingOrderQueue,
    PendingOrder,
    SweepStats,
    serve_healthz,
)


# ================================================================== #
# Helpers
# ================================================================== #
DEFAULT_WORKER_STATS = {
    "claimed": 0, "filled": 0, "rejected": 0, "cancelled": 0,
    "partially_filled": 0, "dispatcher_errors": 0,
    "guard_denies": 0, "guard_scales": 0,
    "side_effects_invoked": 0, "side_effect_errors": 0,
}


async def _spawn(
    *,
    worker_stats=lambda: DEFAULT_WORKER_STATS,
    queue_provider=None,
    sweep_stats=None,
):
    if queue_provider is None:
        q = InMemoryPendingOrderQueue()
        queue_provider = lambda: q
    server = await serve_healthz(
        port=0, host="127.0.0.1",
        worker_stats=worker_stats,
        queue_provider=queue_provider,
        sweep_stats=sweep_stats,
    )
    port = server.sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


async def _close(server):
    server.close()
    try:
        await asyncio.wait_for(server.wait_closed(), timeout=2.0)
    except asyncio.TimeoutError:
        pass


def _parse_metrics(text: str) -> dict[str, float]:
    """Tiny Prometheus text parser — sample lines into a {name: value} dict.
    Ignores HELP/TYPE/comment lines + labels (we don't emit any)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # name value  (no labels in our exposition)
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = float(parts[1])
            except ValueError:
                continue
    return out


# ================================================================== #
# Content-Type + status
# ================================================================== #
@pytest.mark.asyncio
async def test_metrics_returns_200_with_correct_content_type():
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        assert r.status_code == 200
        # Prometheus convention: text/plain version=0.0.4
        ctype = r.headers["content-type"]
        assert "text/plain" in ctype
        assert "version=0.0.4" in ctype
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_payload_ends_with_newline():
    """Prometheus parser requires trailing newline."""
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        assert r.text.endswith("\n")
    finally:
        await _close(server)


# ================================================================== #
# Always-present metrics
# ================================================================== #
@pytest.mark.asyncio
async def test_metrics_includes_uptime_gauge():
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert "worker_uptime_seconds" in m
        assert m["worker_uptime_seconds"] >= 0
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_includes_queue_depth_gauge():
    q = InMemoryPendingOrderQueue()
    for _ in range(5):
        q.enqueue(PendingOrder(
            strategy_id="s", symbol="X", side="long",
            target_notional_usd=100, mode="shadow",
        ))
    server, base = await _spawn(queue_provider=lambda: q)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert m["pending_orders_pending_depth"] == 5
        assert m["queue_probe_ok"] == 1
    finally:
        await _close(server)


# ================================================================== #
# Worker counters render
# ================================================================== #
@pytest.mark.asyncio
async def test_metrics_renders_worker_counters():
    stats = {
        **DEFAULT_WORKER_STATS,
        "claimed": 12, "filled": 8, "rejected": 3,
        "guard_denies": 2, "guard_scales": 1,
    }
    server, base = await _spawn(worker_stats=lambda: stats)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert m["worker_orders_claimed_total"] == 12
        assert m["worker_orders_filled_total"] == 8
        assert m["worker_orders_rejected_total"] == 3
        assert m["worker_guard_denies_total"] == 2
        assert m["worker_guard_scales_total"] == 1
        assert m["worker_stats_ok"] == 1
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_unknown_worker_keys_skipped_not_crashed():
    """Worker stats may include keys we don't have a metric for —
    skip silently rather than crash the endpoint."""
    stats = {**DEFAULT_WORKER_STATS, "future_unknown_stat": 999}
    server, base = await _spawn(worker_stats=lambda: stats)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert "worker_orders_claimed_total" in m   # known stat still emits
        # Unknown stat doesn't show up — by design
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_missing_worker_stat_omits_series():
    """If a worker_stats dict misses one of the expected keys (e.g. older
    PendingOrderWorker version), that one series is absent rather than
    showing 0 — avoids gauges-look-like-counters problem during rollouts."""
    partial = {"claimed": 5, "filled": 3}   # missing many keys
    server, base = await _spawn(worker_stats=lambda: partial)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert "worker_orders_claimed_total" in m
        assert "worker_orders_filled_total" in m
        # absent keys -> series absent
        assert "worker_orders_rejected_total" not in m
        assert "worker_guard_denies_total" not in m
    finally:
        await _close(server)


# ================================================================== #
# Sweeper counters render
# ================================================================== #
@pytest.mark.asyncio
async def test_metrics_renders_sweep_counters():
    sweep = SweepStats(iterations=42, total_expired=7, errors=1)
    server, base = await _spawn(sweep_stats=lambda: sweep)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert m["sweeper_iterations_total"] == 42
        assert m["sweeper_orders_expired_total"] == 7
        assert m["sweeper_errors_total"] == 1
        assert m["sweep_stats_ok"] == 1
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_handles_no_sweep_provider():
    """sweep_stats=None when sweeper isn't enabled. Series absent + ok=1
    (we don't have evidence of failure when no provider was configured)."""
    server, base = await _spawn(sweep_stats=None)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert "sweeper_iterations_total" not in m
        # When provider is None we never tried → ok=1 (no failure observed)
        assert m["sweep_stats_ok"] == 1
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_handles_sweep_provider_returning_none():
    """sweeper sidecar configured but hasn't completed yet → provider
    returns None → series absent but sweep_stats_ok still 1."""
    server, base = await _spawn(sweep_stats=lambda: None)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert "sweeper_iterations_total" not in m
        assert m["sweep_stats_ok"] == 1
    finally:
        await _close(server)


# ================================================================== #
# Probe failure surfacing
# ================================================================== #
@pytest.mark.asyncio
async def test_metrics_worker_stats_failure_sets_ok_to_zero():
    def bad():
        raise RuntimeError("worker dead")
    server, base = await _spawn(worker_stats=bad)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        # Endpoint still 200 — observability is the point
        assert r.status_code == 200
        m = _parse_metrics(r.text)
        assert m["worker_stats_ok"] == 0
        # All counter series omitted
        assert "worker_orders_claimed_total" not in m
        # Uptime gauge is independent — still present
        assert "worker_uptime_seconds" in m
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_queue_failure_sets_probe_ok_to_zero():
    class Boom:
        def list_recent(self, **_):
            raise ConnectionError("DB gone")
    server, base = await _spawn(queue_provider=lambda: Boom())
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert m["queue_probe_ok"] == 0
        assert "pending_orders_pending_depth" not in m
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_metrics_sweep_failure_sets_ok_to_zero():
    def bad():
        raise RuntimeError("sweep stats provider explodes")
    server, base = await _spawn(sweep_stats=bad)
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        m = _parse_metrics(r.text)
        assert m["sweep_stats_ok"] == 0
        assert "sweeper_iterations_total" not in m
    finally:
        await _close(server)


# ================================================================== #
# Format compliance
# ================================================================== #
@pytest.mark.asyncio
async def test_every_emitted_metric_has_help_and_type():
    """Prometheus convention: every metric series must be preceded by
    HELP + TYPE comments somewhere in the payload."""
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics", timeout=2.0)
        text = r.text
        # Every metric name appearing as a series should also appear in a
        # HELP and a TYPE line.
        emitted_names = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts:
                emitted_names.add(parts[0].split("{")[0])

        for name in emitted_names:
            assert f"# HELP {name} " in text, f"missing HELP for {name}"
            assert f"# TYPE {name} " in text, f"missing TYPE for {name}"
    finally:
        await _close(server)


@pytest.mark.asyncio
async def test_query_string_routes_to_metrics():
    """Scrapers commonly hit /metrics?collect[]=foo style queries."""
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base}/metrics?from=prom", timeout=2.0)
        assert r.status_code == 200
        assert "worker_uptime_seconds" in r.text
    finally:
        await _close(server)


# ================================================================== #
# Existing endpoints still work after adding /metrics
# ================================================================== #
@pytest.mark.asyncio
async def test_other_endpoints_still_work():
    server, base = await _spawn()
    try:
        async with httpx.AsyncClient() as client:
            for path in ("/healthz", "/ready", "/stats"):
                r = await client.get(f"{base}{path}", timeout=2.0)
                assert r.status_code == 200, f"{path} failed"
                assert r.headers["content-type"].startswith("application/json")
    finally:
        await _close(server)
