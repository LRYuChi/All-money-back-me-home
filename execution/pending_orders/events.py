"""EventLogger — appends to pending_order_events on every status transition.

Migration 020 shipped the `pending_order_events` table for replayable
audit ("why was this order cancelled?") but no writer was wired until
round 36. This module fills the gap.

Hooked into PendingOrderQueue:
  - enqueue:            from_status=None → to_status=PENDING (initial)
  - claim_next_pending: PENDING → DISPATCHING
  - update_status:      <current> → <new> (with optional reason + detail)

Fire-and-forget — backend failures log + swallow. Matches credential audit
pattern (round 34): an audit-table outage must not block trading. The
gap is visible by counting events vs orders in the dashboard.

Backends mirror the queue: NoOp / InMemory / Postgres / Supabase.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from execution.pending_orders.types import PendingOrderStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class OrderEvent:
    """One row from `pending_order_events`. Returned by `history()`."""

    order_id: int
    from_status: PendingOrderStatus | None     # None = initial enqueue
    to_status: PendingOrderStatus
    reason: str | None
    detail: dict | None
    created_at: datetime


class EventLogger(Protocol):
    def record(
        self,
        order_id: int,
        from_status: PendingOrderStatus | None,
        to_status: PendingOrderStatus,
        *,
        reason: str | None = None,
        detail: dict | None = None,
    ) -> None: ...

    def history(
        self, order_id: int, *, limit: int = 100,
    ) -> list[OrderEvent]: ...


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpEventLogger:
    """Discards all events. Default when no DB is configured."""

    def record(
        self, order_id, from_status, to_status, *,
        reason=None, detail=None,
    ) -> None:
        pass

    def history(self, order_id: int, *, limit: int = 100) -> list[OrderEvent]:
        return []


# ================================================================== #
# InMemory — for tests
# ================================================================== #
class InMemoryEventLogger:
    """Caller can inspect `events` list for assertions."""

    def __init__(self) -> None:
        self.events: list[OrderEvent] = []

    def record(
        self, order_id, from_status, to_status, *,
        reason=None, detail=None,
    ) -> None:
        # Defensive coercion — caller may pass status as str
        ts = _coerce_status(to_status)
        fs = _coerce_status(from_status) if from_status is not None else None
        self.events.append(OrderEvent(
            order_id=int(order_id),
            from_status=fs, to_status=ts,
            reason=reason,
            detail=dict(detail) if detail else None,
            created_at=datetime.now(timezone.utc),
        ))

    def history(
        self, order_id: int, *, limit: int = 100,
    ) -> list[OrderEvent]:
        rows = [e for e in self.events if e.order_id == order_id]
        # Newest first to match Postgres index `created_at desc`
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]


# ================================================================== #
# Postgres
# ================================================================== #
class PostgresEventLogger:
    TABLE = "pending_order_events"

    def __init__(self, dsn: str):
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def record(
        self, order_id, from_status, to_status, *,
        reason=None, detail=None,
    ) -> None:
        try:
            import json
            ts = _coerce_status(to_status).value
            fs = _coerce_status(from_status).value if from_status is not None else None
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"insert into {self.TABLE} "
                    "(order_id, from_status, to_status, reason, detail, created_at) "
                    "values (%s, %s, %s, %s, %s::jsonb, now())",
                    (int(order_id), fs, ts, reason,
                     json.dumps(detail) if detail else None),
                )
                conn.commit()
        except Exception as e:
            logger.warning(
                "event_logger: PG insert failed (%s): order=%s %s→%s",
                e, order_id, from_status, to_status,
            )

    def history(
        self, order_id: int, *, limit: int = 100,
    ) -> list[OrderEvent]:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"select order_id, from_status, to_status, reason, detail, created_at "
                    f"from {self.TABLE} where order_id = %s "
                    f"order by created_at desc limit %s",
                    (int(order_id), limit),
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.warning("event_logger: PG history failed: %s", e)
            return []
        return [
            OrderEvent(
                order_id=int(r[0]),
                from_status=PendingOrderStatus(r[1]) if r[1] else None,
                to_status=PendingOrderStatus(r[2]),
                reason=r[3], detail=r[4],
                created_at=r[5],
            )
            for r in rows
        ]


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabaseEventLogger:
    TABLE = "pending_order_events"

    def __init__(self, client: Any):
        self._client = client

    def record(
        self, order_id, from_status, to_status, *,
        reason=None, detail=None,
    ) -> None:
        try:
            ts = _coerce_status(to_status).value
            fs = _coerce_status(from_status).value if from_status is not None else None
            self._client.table(self.TABLE).insert({
                "order_id": int(order_id),
                "from_status": fs,
                "to_status": ts,
                "reason": reason,
                "detail": detail,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(
                "event_logger: Supabase insert failed (%s): order=%s %s→%s",
                e, order_id, from_status, to_status,
            )

    def history(
        self, order_id: int, *, limit: int = 100,
    ) -> list[OrderEvent]:
        try:
            res = (
                self._client.table(self.TABLE).select("*")
                .eq("order_id", int(order_id))
                .order("created_at", desc=True).limit(limit).execute()
            )
        except Exception as e:
            logger.warning("event_logger: Supabase history failed: %s", e)
            return []
        return [_row_to_event(r) for r in (res.data or [])]


def _row_to_event(row: dict) -> OrderEvent:
    return OrderEvent(
        order_id=int(row["order_id"]),
        from_status=PendingOrderStatus(row["from_status"]) if row.get("from_status") else None,
        to_status=PendingOrderStatus(row["to_status"]),
        reason=row.get("reason"),
        detail=row.get("detail"),
        created_at=_parse_iso(row.get("created_at"))
                   or datetime.now(timezone.utc),
    )


def _parse_iso(s) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _coerce_status(s) -> PendingOrderStatus:
    """Accept enum or raw string."""
    if isinstance(s, PendingOrderStatus):
        return s
    return PendingOrderStatus(s)


# ================================================================== #
# Factory
# ================================================================== #
def build_event_logger(settings) -> EventLogger:  # noqa: ANN001
    """Pick the matching backend for the configured DB."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        return PostgresEventLogger(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            return SupabaseEventLogger(create_client(sb_url, sb_key))
        except ImportError:
            pass

    return NoOpEventLogger()


__all__ = [
    "EventLogger",
    "OrderEvent",
    "NoOpEventLogger",
    "InMemoryEventLogger",
    "PostgresEventLogger",
    "SupabaseEventLogger",
    "build_event_logger",
]
