"""SignalAgeProvider — look up signal age (seconds) for a pending order.

G1 LatencyBudgetGuard needs `signal_age_seconds` on the GuardContext to
decide whether the alpha is stale. Until round 23 the worker passed
None, so G1 always fail-opened. This module provides the lookup:

    PendingOrder.fused_signal_id → fused_signals.ts → (now - ts).seconds

Backends mirror PnLAggregator / ExposureProvider:
  - NoOp                  — always None (G1 stays fail-open)
  - InMemory              — caller pre-loads (id, ts) pairs (tests)
  - Supabase REST         — single-row select on fused_signals.id
  - Postgres direct       — same with a parameterised query
  - Factory               — pick best backend from settings

Notes:
  - When `order.fused_signal_id` is None we return None (G1 fail-open).
  - When the lookup raises we return None — same fail-open semantic.
  - Backends cache hits per-id within an instance to avoid re-querying
    the same fused_signal across consecutive orders in one tick. Caller
    can call `refresh()` to clear.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

from execution.pending_orders.types import PendingOrder

logger = logging.getLogger(__name__)


class SignalAgeProvider(Protocol):
    def age_seconds(
        self, order: PendingOrder, *, now: datetime | None = None,
    ) -> float | None: ...


def _seconds_since(ts: datetime, now: datetime | None = None) -> float:
    n = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = n - ts.astimezone(timezone.utc)
    return delta.total_seconds()


def _parse_ts(value: Any) -> datetime | None:
    """Normalise a Supabase/PG timestamptz response to a UTC-aware dt."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            # Supabase REST returns ISO strings like "2026-04-25T13:45:00+00:00"
            # or "...Z". datetime.fromisoformat handles +00:00 directly;
            # 'Z' suffix needs swapping.
            normalised = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalised)
        except ValueError:
            return None
    return None


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpSignalAgeProvider:
    """Always returns None → G1 fail-opens. Use when no DB / no fusion
    layer wired yet."""

    def age_seconds(
        self, order: PendingOrder, *, now: datetime | None = None,
    ) -> float | None:
        return None


# ================================================================== #
# InMemory — for tests
# ================================================================== #
class InMemorySignalAgeProvider:
    """Caller seeds (fused_signal_id → ts) pairs. Returns age relative
    to `now` (defaults to datetime.now)."""

    def __init__(self, signals: dict[int, datetime] | None = None):
        self._by_id: dict[int, datetime] = {}
        for sid, ts in (signals or {}).items():
            self.add(sid, ts)

    def add(self, signal_id: int, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        self._by_id[int(signal_id)] = ts.astimezone(timezone.utc)

    def age_seconds(
        self, order: PendingOrder, *, now: datetime | None = None,
    ) -> float | None:
        if order.fused_signal_id is None:
            return None
        ts = self._by_id.get(int(order.fused_signal_id))
        if ts is None:
            return None
        return _seconds_since(ts, now)


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabaseSignalAgeProvider:
    """Single-row select on fused_signals.id for the order's fused_signal_id.

    Caches resolved ts per id within the instance lifetime — the same
    fused_signal_id is unlikely to age differently between two consecutive
    lookups. Call `refresh()` to bust the cache.
    """

    TABLE = "fused_signals"

    def __init__(self, client: Any):
        self._client = client
        self._cache: dict[int, datetime | None] = {}

    def refresh(self) -> None:
        self._cache.clear()

    def age_seconds(
        self, order: PendingOrder, *, now: datetime | None = None,
    ) -> float | None:
        sid = order.fused_signal_id
        if sid is None:
            return None
        sid = int(sid)

        if sid in self._cache:
            ts = self._cache[sid]
            return _seconds_since(ts, now) if ts is not None else None

        ts = self._fetch(sid)
        self._cache[sid] = ts
        return _seconds_since(ts, now) if ts is not None else None

    def _fetch(self, sid: int) -> datetime | None:
        try:
            res = (
                self._client.table(self.TABLE).select("ts")
                .eq("id", sid).limit(1).execute()
            )
            rows = res.data or []
            if not rows:
                logger.debug("signal_age: fused_signal id=%s not found", sid)
                return None
            return _parse_ts(rows[0].get("ts"))
        except Exception as e:
            logger.warning(
                "signal_age: fused_signals lookup failed for id=%s (%s)",
                sid, e,
            )
            return None


# ================================================================== #
# Postgres direct
# ================================================================== #
class PostgresSignalAgeProvider:
    SQL = "select ts from fused_signals where id = %s limit 1"

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._cache: dict[int, datetime | None] = {}

    def refresh(self) -> None:
        self._cache.clear()

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def age_seconds(
        self, order: PendingOrder, *, now: datetime | None = None,
    ) -> float | None:
        sid = order.fused_signal_id
        if sid is None:
            return None
        sid = int(sid)

        if sid in self._cache:
            ts = self._cache[sid]
            return _seconds_since(ts, now) if ts is not None else None

        ts = self._fetch(sid)
        self._cache[sid] = ts
        return _seconds_since(ts, now) if ts is not None else None

    def _fetch(self, sid: int) -> datetime | None:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(self.SQL, (sid,))
                row = cur.fetchone()
                if row is None or row[0] is None:
                    return None
                return _parse_ts(row[0])
        except Exception as e:
            logger.warning(
                "signal_age: PG lookup failed for id=%s (%s)", sid, e,
            )
            return None


# ================================================================== #
# Factory
# ================================================================== #
def build_signal_age_provider(settings) -> SignalAgeProvider:  # noqa: ANN001
    """Postgres > Supabase > NoOp. Mirrors PnLAggregator / ExposureProvider."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("signal_age_provider: PostgresSignalAgeProvider")
        return PostgresSignalAgeProvider(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("signal_age_provider: SupabaseSignalAgeProvider")
            return SupabaseSignalAgeProvider(client)
        except ImportError:
            logger.warning("signal_age_provider: supabase-py missing")

    logger.warning(
        "signal_age_provider: NoOp (no DB) — G1 LatencyBudgetGuard will "
        "always fail-open"
    )
    return NoOpSignalAgeProvider()


__all__ = [
    "SignalAgeProvider",
    "NoOpSignalAgeProvider",
    "InMemorySignalAgeProvider",
    "SupabaseSignalAgeProvider",
    "PostgresSignalAgeProvider",
    "build_signal_age_provider",
]
