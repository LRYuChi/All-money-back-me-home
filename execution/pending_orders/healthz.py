"""Minimal HTTP /healthz server for the worker daemon (round 39).

Exposes the worker's accumulated stats over HTTP so ops can probe the
process health without shelling in. Designed for k8s/docker liveness +
readiness probes and Prometheus/Datadog scrapers.

Endpoints:
    GET /healthz   → {"ok": true, "uptime_sec": ...}
                     200 always (process is responsive)
    GET /ready     → {"ready": bool, "queue_ok": bool, "depth": int}
                     200 if queue.list_recent succeeds, 503 otherwise
    GET /stats     → {worker: stats_dict, sweep: stats_dict|null,
                      queue_depth: int, uptime_sec: float}
                     200 always
    GET /metrics   → Prometheus 0.0.4 text exposition format
                     200 always (probe failures surface as
                     worker_stats_ok / queue_probe_ok / sweep_stats_ok = 0)

Implementation uses asyncio.start_server + a tiny HTTP/1.0 parser to
avoid pulling aiohttp/FastAPI into the trading-engine deps. Server is
HTTP/1.0 (no keep-alive) — one request per connection, plenty for a
~1Hz health probe.

Lifecycle: `serve_healthz(port, ...)` returns an awaitable; cancel it
to shut down. The worker CLI integrates this as an asyncio task
alongside the dispatch loop, sharing the same stop_event semantics.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)


# Caller supplies these. Both Optional — None values render as null.
WorkerStatsProvider = Callable[[], dict[str, int]]
SweepStatsProvider = Callable[[], Any]   # SweepStats | None
QueueProvider = Callable[[], Any]        # PendingOrderQueue


class _HealthzState:
    """Holds the references the request handlers read from."""

    def __init__(
        self,
        *,
        worker_stats: WorkerStatsProvider,
        queue_provider: QueueProvider,
        sweep_stats: SweepStatsProvider | None = None,
    ):
        self.worker_stats = worker_stats
        self.queue_provider = queue_provider
        self.sweep_stats = sweep_stats
        self.started_at = time.monotonic()


def _http_response(
    status_line: str,
    body: dict | str,
    *,
    content_type: str = "application/json",
) -> bytes:
    """Pack a minimal HTTP/1.0 response. `Connection: close` so the
    client doesn't try to reuse the socket."""
    if isinstance(body, dict):
        payload = json.dumps(body, default=str).encode("utf-8")
    else:
        payload = body.encode("utf-8")
    headers = (
        f"HTTP/1.0 {status_line}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(payload)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii")
    return headers + payload


def _parse_request_line(line: bytes) -> tuple[str, str] | None:
    """Returns (method, path) or None on parse failure."""
    try:
        text = line.decode("ascii", errors="replace").strip()
    except Exception:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    return parts[0].upper(), parts[1]


async def _read_request_line(reader: asyncio.StreamReader) -> bytes | None:
    """Read the first line, with a 1s deadline so a slow/stuck client
    can't hold a worker thread."""
    try:
        return await asyncio.wait_for(reader.readline(), timeout=1.0)
    except (asyncio.TimeoutError, ConnectionError):
        return None


def _route(state: _HealthzState, path: str) -> bytes:
    if path == "/healthz" or path.startswith("/healthz?"):
        return _http_response("200 OK", {
            "ok": True,
            "uptime_sec": round(time.monotonic() - state.started_at, 3),
        })

    if path == "/ready" or path.startswith("/ready?"):
        depth, queue_ok = _probe_queue(state.queue_provider)
        status_line = "200 OK" if queue_ok else "503 Service Unavailable"
        return _http_response(status_line, {
            "ready": queue_ok,
            "queue_ok": queue_ok,
            "depth": depth,
        })

    if path == "/stats" or path.startswith("/stats?"):
        sweep = None
        if state.sweep_stats is not None:
            try:
                sweep_obj = state.sweep_stats()
                sweep = _sweep_to_dict(sweep_obj)
            except Exception as e:
                logger.debug("/stats: sweep probe failed: %s", e)
                sweep = {"error": str(e)}

        depth, _ok = _probe_queue(state.queue_provider)

        try:
            worker = dict(state.worker_stats())
        except Exception as e:
            logger.warning("/stats: worker stats probe failed: %s", e)
            worker = {"error": str(e)}

        return _http_response("200 OK", {
            "worker": worker,
            "sweep": sweep,
            "queue_depth": depth,
            "uptime_sec": round(time.monotonic() - state.started_at, 3),
        })

    if path == "/metrics" or path.startswith("/metrics?"):
        return _http_response(
            "200 OK",
            _render_prometheus(state),
            content_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return _http_response("404 Not Found", {"error": f"unknown path {path!r}"})


def _probe_queue(queue_provider: QueueProvider) -> tuple[int, bool]:
    """Returns (depth, queue_ok). depth=-1 + queue_ok=False on any failure
    (provider call, list_recent call, or import). Used by /ready and /stats."""
    try:
        queue = queue_provider()
        from execution.pending_orders.types import PendingOrderStatus
        rows = queue.list_recent(limit=1000, status=PendingOrderStatus.PENDING)
        return len(rows), True
    except Exception as e:
        logger.warning("queue probe failed: %s", e)
        return -1, False


def _sweep_to_dict(sweep_obj: Any) -> dict:
    """SweepStats dataclass → dict. Tolerant of None / random objects."""
    if sweep_obj is None:
        return {}
    if isinstance(sweep_obj, dict):
        return sweep_obj
    # Try dataclass __dict__ / asdict
    try:
        import dataclasses
        if dataclasses.is_dataclass(sweep_obj):
            return dataclasses.asdict(sweep_obj)
    except Exception:
        pass
    return {"repr": repr(sweep_obj)}


# ================================================================== #
# Prometheus exposition format (round 40)
# ================================================================== #
# Maps worker stat keys (from PendingOrderWorker.stats()) into:
#   - counters: monotonically-increasing totals (claims, fills, errors)
#   - gauges: point-in-time values (none currently from worker)
#
# Worker stats schema (round 17 + 19 + 26):
#   claimed, filled, rejected, cancelled, partially_filled,
#   dispatcher_errors, guard_denies, guard_scales,
#   side_effects_invoked, side_effect_errors

_WORKER_COUNTERS: dict[str, tuple[str, str]] = {
    # stat_key → (metric_name, help_text)
    "claimed":              ("worker_orders_claimed_total",
                             "Total orders claimed from queue by worker"),
    "filled":               ("worker_orders_filled_total",
                             "Total orders that reached FILLED terminal"),
    "rejected":             ("worker_orders_rejected_total",
                             "Total orders that reached REJECTED terminal"),
    "cancelled":            ("worker_orders_cancelled_total",
                             "Total orders that reached CANCELLED terminal"),
    "partially_filled":     ("worker_orders_partially_filled_total",
                             "Total orders that hit PARTIALLY_FILLED"),
    "dispatcher_errors":    ("worker_dispatcher_errors_total",
                             "Total dispatcher exceptions caught"),
    "guard_denies":         ("worker_guard_denies_total",
                             "Total guard pipeline DENY decisions"),
    "guard_scales":         ("worker_guard_scales_total",
                             "Total guard pipeline SCALE decisions"),
    "side_effects_invoked": ("worker_side_effects_invoked_total",
                             "Total guard side-effect handler invocations"),
    "side_effect_errors":   ("worker_side_effect_errors_total",
                             "Total guard side-effect handler exceptions"),
}

_SWEEP_COUNTERS: dict[str, tuple[str, str]] = {
    "iterations":    ("sweeper_iterations_total",
                      "Total sweeper iterations (one per --sweep-interval-sec tick)"),
    "total_expired": ("sweeper_orders_expired_total",
                      "Total orders moved to EXPIRED by sweeper"),
    "errors":        ("sweeper_errors_total",
                      "Total sweep_expired() exceptions caught + swallowed"),
}


def _render_prometheus(state: _HealthzState) -> str:
    """Render all metrics in Prometheus 0.0.4 text exposition format.

    Always returns a complete payload — provider exceptions are converted
    into per-metric absences (ops sees missing series, not a 500). The
    `worker_stats_ok` / `sweep_stats_ok` / `queue_probe_ok` gauges let
    Prometheus alert on probe failure.
    """
    lines: list[str] = []

    # --- Uptime gauge (always present) ---
    lines.append("# HELP worker_uptime_seconds Process uptime since worker start")
    lines.append("# TYPE worker_uptime_seconds gauge")
    lines.append(f"worker_uptime_seconds {time.monotonic() - state.started_at:.3f}")

    # --- Worker counters ---
    worker_ok = 1
    worker_dict: dict[str, int] = {}
    try:
        worker_dict = dict(state.worker_stats())
    except Exception as e:
        logger.warning("/metrics: worker stats probe failed: %s", e)
        worker_ok = 0

    for key, (metric_name, help_text) in _WORKER_COUNTERS.items():
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} counter")
        if key in worker_dict:
            lines.append(f"{metric_name} {int(worker_dict[key])}")
        # If the stat key is missing, intentionally don't emit the series —
        # rolling out a new counter shouldn't break dashboards that
        # `or vector(0)` it.

    lines.append("# HELP worker_stats_ok 1 if worker stats probe succeeded, 0 otherwise")
    lines.append("# TYPE worker_stats_ok gauge")
    lines.append(f"worker_stats_ok {worker_ok}")

    # --- Sweep counters ---
    sweep_ok = 1
    sweep_dict: dict = {}
    if state.sweep_stats is not None:
        try:
            sweep_obj = state.sweep_stats()
            if sweep_obj is not None:
                sweep_dict = _sweep_to_dict(sweep_obj)
            else:
                # Sweeper hasn't reported yet — series intentionally absent
                sweep_dict = {}
        except Exception as e:
            logger.debug("/metrics: sweep probe failed: %s", e)
            sweep_ok = 0

    for key, (metric_name, help_text) in _SWEEP_COUNTERS.items():
        lines.append(f"# HELP {metric_name} {help_text}")
        lines.append(f"# TYPE {metric_name} counter")
        if key in sweep_dict:
            lines.append(f"{metric_name} {int(sweep_dict[key])}")

    lines.append("# HELP sweep_stats_ok 1 if sweep stats provider returned successfully, 0 otherwise")
    lines.append("# TYPE sweep_stats_ok gauge")
    lines.append(f"sweep_stats_ok {sweep_ok}")

    # --- Queue depth gauge ---
    depth, queue_ok = _probe_queue(state.queue_provider)
    lines.append("# HELP pending_orders_pending_depth Current PENDING queue depth (sampled)")
    lines.append("# TYPE pending_orders_pending_depth gauge")
    if queue_ok:
        lines.append(f"pending_orders_pending_depth {depth}")
    lines.append("# HELP queue_probe_ok 1 if queue.list_recent succeeded, 0 otherwise")
    lines.append("# TYPE queue_probe_ok gauge")
    lines.append(f"queue_probe_ok {1 if queue_ok else 0}")

    # Trailing newline is required by Prometheus
    return "\n".join(lines) + "\n"


# ================================================================== #
# Server entrypoint
# ================================================================== #
async def serve_healthz(
    *,
    port: int,
    worker_stats: WorkerStatsProvider,
    queue_provider: QueueProvider,
    sweep_stats: SweepStatsProvider | None = None,
    host: str = "127.0.0.1",
) -> asyncio.Server:
    """Start the healthz server. Returns the asyncio.Server — caller
    closes it on shutdown.

    `host` defaults to localhost only — k8s probes hit the pod's local
    interface. Set to "0.0.0.0" if you need external scraping (with
    appropriate firewall rules).
    """
    state = _HealthzState(
        worker_stats=worker_stats,
        queue_provider=queue_provider,
        sweep_stats=sweep_stats,
    )

    async def _handle(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            line = await _read_request_line(reader)
            if line is None:
                return
            parsed = _parse_request_line(line)
            if parsed is None:
                writer.write(_http_response("400 Bad Request", {"error": "bad request line"}))
            else:
                method, path = parsed
                if method != "GET":
                    writer.write(_http_response(
                        "405 Method Not Allowed",
                        {"error": f"only GET supported; got {method}"},
                    ))
                else:
                    writer.write(_route(state, path))
            await writer.drain()
        except Exception as e:
            logger.warning("healthz handler error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    server = await asyncio.start_server(_handle, host=host, port=port)
    logger.info("healthz server listening on %s:%d", host, port)
    return server


__all__ = [
    "QueueProvider",
    "SweepStatsProvider",
    "WorkerStatsProvider",
    "serve_healthz",
]
