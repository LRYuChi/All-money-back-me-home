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
