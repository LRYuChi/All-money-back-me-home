"""PendingOrderQueue Protocol + 4 backends + factory.

Same backend pattern as shared.signals.history / snapshots / credentials:
NoOp / InMemory / Supabase / Postgres + factory selecting by settings.

Workers (Phase F.1+) poll `claim_next_pending(mode)` to pick up work
atomically — implementations must be safe for multiple consumers when
that becomes relevant (Phase H scale-out). For now Postgres uses a
SELECT FOR UPDATE SKIP LOCKED pattern; InMemory uses a simple lock.

Round 36: every backend now takes an optional `event_logger` and writes
to `pending_order_events` on every status transition (initial enqueue,
claim, terminal). Failures are fire-and-forget to keep trading flowing
when the audit table has a hiccup.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any, Protocol

from execution.pending_orders.events import (
    EventLogger,
    NoOpEventLogger,
    build_event_logger,
)
from execution.pending_orders.types import (
    ExecutionMode,
    PendingOrder,
    PendingOrderStatus,
)

logger = logging.getLogger(__name__)


class PendingOrderNotFound(KeyError):
    """No row with that id."""


def _safe_log_event(
    event_logger: EventLogger | None,
    order_id: int | None,
    from_status: PendingOrderStatus | None,
    to_status: PendingOrderStatus,
    *,
    reason: str | None = None,
    detail: dict | None = None,
) -> None:
    """Defence: never let an event logger failure break the trade path."""
    if event_logger is None or order_id is None:
        return
    try:
        event_logger.record(
            order_id, from_status, to_status,
            reason=reason, detail=detail,
        )
    except Exception as e:
        logger.warning(
            "event_logger.record raised (%s) for order %s %s→%s — swallowing",
            e, order_id, from_status, to_status,
        )


class PendingOrderQueue(Protocol):
    def enqueue(self, order: PendingOrder) -> int: ...
    def get(self, order_id: int) -> PendingOrder: ...
    def claim_next_pending(self, mode: ExecutionMode) -> PendingOrder | None: ...
    def update_status(
        self,
        order_id: int,
        status: PendingOrderStatus,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None: ...
    def list_recent(
        self,
        *,
        limit: int = 100,
        status: PendingOrderStatus | None = None,
    ) -> list[PendingOrder]: ...
    def sweep_expired(
        self,
        *,
        pending_max_age_sec: float = 0,
        dispatching_max_age_sec: float = 0,
    ) -> int: ...


# ================================================================== #
# NoOp — for environments where queue is intentionally disabled
# ================================================================== #
class NoOpPendingOrderQueue:
    """Discard. Logs at INFO so caller knows order was emitted but not
    persisted (e.g. shadow daemon in dev mode without DB)."""

    def enqueue(self, order: PendingOrder) -> int:
        logger.info(
            "pending_order (no-op): strategy=%s %s %s notional=%.2f mode=%s",
            order.strategy_id, order.symbol, order.side,
            order.target_notional_usd, order.mode,
        )
        return 0

    def get(self, order_id: int) -> PendingOrder:
        raise PendingOrderNotFound(order_id)

    def claim_next_pending(self, mode: ExecutionMode) -> PendingOrder | None:
        return None

    def update_status(self, order_id, status, *, last_error=None, increment_attempts=False):
        return

    def list_recent(self, *, limit=100, status=None) -> list[PendingOrder]:
        return []

    def sweep_expired(self, *, pending_max_age_sec=0, dispatching_max_age_sec=0):
        return 0


# ================================================================== #
# InMemory — tests + smoke
# ================================================================== #
class InMemoryPendingOrderQueue:
    """Thread-safe dict-backed queue. Workers can claim via FIFO order."""

    def __init__(self, *, event_logger: EventLogger | None = None) -> None:
        self._orders: dict[int, PendingOrder] = {}
        self._next_id: int = 1
        self._lock = threading.Lock()
        self._events = event_logger or NoOpEventLogger()

    def enqueue(self, order: PendingOrder) -> int:
        with self._lock:
            # Idempotency: if client_order_id matches existing, return its id
            if order.client_order_id is not None:
                for existing in self._orders.values():
                    if existing.client_order_id == order.client_order_id:
                        return existing.id  # type: ignore[return-value]
            order.id = self._next_id
            self._next_id += 1
            self._orders[order.id] = order
        # Event recorded outside the lock so a slow audit can't stall enqueues
        _safe_log_event(
            self._events, order.id, None, PendingOrderStatus.PENDING,
            reason="initial enqueue",
            detail={"strategy_id": order.strategy_id, "symbol": order.symbol,
                    "side": order.side, "mode": order.mode,
                    "notional_usd": order.target_notional_usd},
        )
        return order.id

    def get(self, order_id: int) -> PendingOrder:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise PendingOrderNotFound(order_id)
            return order

    def claim_next_pending(self, mode: ExecutionMode) -> PendingOrder | None:
        claimed: PendingOrder | None = None
        with self._lock:
            for order in sorted(self._orders.values(), key=lambda o: o.created_at):
                if order.status == PendingOrderStatus.PENDING and order.mode == mode:
                    order.status = PendingOrderStatus.DISPATCHING
                    order.attempts += 1
                    order.dispatched_at = datetime.now(timezone.utc)
                    order.updated_at = datetime.now(timezone.utc)
                    claimed = order
                    break
        if claimed is not None:
            _safe_log_event(
                self._events, claimed.id,
                PendingOrderStatus.PENDING, PendingOrderStatus.DISPATCHING,
                reason="claimed by worker",
                detail={"attempt": claimed.attempts},
            )
        return claimed

    def update_status(
        self,
        order_id: int,
        status: PendingOrderStatus,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise PendingOrderNotFound(order_id)
            from_status = order.status
            order.status = status
            if last_error is not None:
                order.last_error = last_error
            if increment_attempts:
                order.attempts += 1
            order.updated_at = datetime.now(timezone.utc)
            if order.is_terminal:
                order.completed_at = order.updated_at
        if from_status != status:
            _safe_log_event(
                self._events, order_id, from_status, status,
                reason=last_error,
            )

    def list_recent(
        self,
        *,
        limit: int = 100,
        status: PendingOrderStatus | None = None,
    ) -> list[PendingOrder]:
        with self._lock:
            rows = sorted(
                self._orders.values(),
                key=lambda o: o.created_at, reverse=True,
            )
            if status is not None:
                rows = [r for r in rows if r.status == status]
            return rows[:limit]

    def sweep_expired(
        self,
        *,
        pending_max_age_sec: float = 0,
        dispatching_max_age_sec: float = 0,
    ) -> int:
        """Move stuck PENDING/DISPATCHING orders to EXPIRED. Returns count.

        Either threshold being 0 (or negative) skips that bucket — caller
        opts in to one direction or both. PENDING expiry catches strategies
        that fire intents the worker mode never services; DISPATCHING expiry
        catches workers that crashed mid-dispatch (the order was claimed
        but never reached a terminal status).
        """
        now = datetime.now(timezone.utc)
        targets: list[tuple[int, PendingOrderStatus, str, dict]] = []

        with self._lock:
            for order in self._orders.values():
                if order.status == PendingOrderStatus.PENDING and pending_max_age_sec > 0:
                    age = (now - order.created_at).total_seconds()
                    if age >= pending_max_age_sec:
                        targets.append((
                            order.id,  # type: ignore[arg-type]
                            PendingOrderStatus.PENDING,
                            f"pending {age:.1f}s ≥ {pending_max_age_sec:.0f}s threshold",
                            {"age_sec": age, "threshold_sec": pending_max_age_sec,
                             "from_status": "pending"},
                        ))
                elif order.status == PendingOrderStatus.DISPATCHING and dispatching_max_age_sec > 0:
                    base_ts = order.dispatched_at or order.updated_at
                    age = (now - base_ts).total_seconds()
                    if age >= dispatching_max_age_sec:
                        targets.append((
                            order.id,  # type: ignore[arg-type]
                            PendingOrderStatus.DISPATCHING,
                            f"dispatching {age:.1f}s ≥ {dispatching_max_age_sec:.0f}s threshold "
                            f"(worker likely crashed)",
                            {"age_sec": age, "threshold_sec": dispatching_max_age_sec,
                             "from_status": "dispatching"},
                        ))

        # Mutate outside the lock — update_status handles its own locking
        # and emits the audit event. Failures per-row don't abort the batch.
        n = 0
        for order_id, _from, reason, _detail in targets:
            try:
                self.update_status(
                    order_id, PendingOrderStatus.EXPIRED, last_error=reason,
                )
                n += 1
            except Exception as e:
                logger.warning(
                    "sweep_expired: update_status(%s, EXPIRED) failed (%s)",
                    order_id, e,
                )
        return n


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabasePendingOrderQueue:
    TABLE = "pending_orders"

    def __init__(
        self, client: Any, *, event_logger: EventLogger | None = None,
    ):
        self._client = client
        self._events = event_logger or NoOpEventLogger()

    def enqueue(self, order: PendingOrder) -> int:
        # Idempotency: if client_order_id supplied + already exists, return its id
        if order.client_order_id is not None:
            existing = (
                self._client.table(self.TABLE).select("id")
                .eq("client_order_id", order.client_order_id)
                .limit(1).execute()
            )
            if existing.data:
                return int(existing.data[0]["id"])

        row = order.to_row()
        # Strip id; DB assigns
        row.pop("id", None)
        res = self._client.table(self.TABLE).insert(row).execute()
        new_id = int(res.data[0]["id"]) if res.data else 0
        order.id = new_id
        _safe_log_event(
            self._events, new_id, None, PendingOrderStatus.PENDING,
            reason="initial enqueue",
            detail={"strategy_id": order.strategy_id, "symbol": order.symbol,
                    "side": order.side, "mode": order.mode,
                    "notional_usd": order.target_notional_usd},
        )
        return new_id

    def get(self, order_id: int) -> PendingOrder:
        res = (
            self._client.table(self.TABLE).select("*")
            .eq("id", order_id).limit(1).execute()
        )
        if not res.data:
            raise PendingOrderNotFound(order_id)
        return _row_to_order(res.data[0])

    def claim_next_pending(self, mode: ExecutionMode) -> PendingOrder | None:
        # Best-effort claim via REST: select one + update status. Not safe
        # for concurrent workers — Phase H switches to PostgresStore for prod.
        res = (
            self._client.table(self.TABLE).select("*")
            .eq("status", "pending").eq("mode", mode)
            .order("created_at").limit(1).execute()
        )
        if not res.data:
            return None
        row = res.data[0]
        order = _row_to_order(row)
        # update_status emits the event for us
        self.update_status(
            order.id, PendingOrderStatus.DISPATCHING, increment_attempts=True,
        )
        order.status = PendingOrderStatus.DISPATCHING
        order.attempts += 1
        return order

    def update_status(
        self,
        order_id: int,
        status: PendingOrderStatus,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        # Read current status BEFORE the update so the event has from_status
        from_status: PendingOrderStatus | None = None
        try:
            res = (
                self._client.table(self.TABLE).select("status")
                .eq("id", order_id).limit(1).execute()
            )
            if res.data:
                from_status = PendingOrderStatus(res.data[0]["status"])
        except Exception:
            pass

        now = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "status": status.value,
            "updated_at": now,
        }
        if status in {PendingOrderStatus.FILLED, PendingOrderStatus.REJECTED,
                      PendingOrderStatus.CANCELLED, PendingOrderStatus.EXPIRED}:
            payload["completed_at"] = now
        if last_error is not None:
            payload["last_error"] = last_error
        # increment_attempts ignored for Supabase (would need rpc); test path uses InMemory
        self._client.table(self.TABLE).update(payload).eq("id", order_id).execute()
        if from_status != status:
            _safe_log_event(
                self._events, order_id, from_status, status,
                reason=last_error,
            )

    def list_recent(
        self,
        *,
        limit: int = 100,
        status: PendingOrderStatus | None = None,
    ) -> list[PendingOrder]:
        q = self._client.table(self.TABLE).select("*").order("created_at", desc=True).limit(limit)
        if status is not None:
            q = q.eq("status", status.value)
        res = q.execute()
        return [_row_to_order(r) for r in (res.data or [])]

    def sweep_expired(
        self,
        *,
        pending_max_age_sec: float = 0,
        dispatching_max_age_sec: float = 0,
    ) -> int:
        from datetime import timedelta as _td
        now = datetime.now(timezone.utc)
        n = 0

        if pending_max_age_sec > 0:
            cutoff = (now - _td(seconds=pending_max_age_sec)).isoformat()
            try:
                res = (
                    self._client.table(self.TABLE).select("id,created_at")
                    .eq("status", "pending").lt("created_at", cutoff)
                    .execute()
                )
                for row in (res.data or []):
                    age = pending_max_age_sec   # caller can grep DB for exact ts
                    self._expire(int(row["id"]),
                                 f"pending ≥ {pending_max_age_sec:.0f}s threshold")
                    n += 1
            except Exception as e:
                logger.warning("sweep_expired (pending) supabase failed: %s", e)

        if dispatching_max_age_sec > 0:
            cutoff = (now - _td(seconds=dispatching_max_age_sec)).isoformat()
            try:
                res = (
                    self._client.table(self.TABLE).select("id,dispatched_at,updated_at")
                    .eq("status", "dispatching").lt("dispatched_at", cutoff)
                    .execute()
                )
                for row in (res.data or []):
                    self._expire(
                        int(row["id"]),
                        f"dispatching ≥ {dispatching_max_age_sec:.0f}s threshold "
                        f"(worker likely crashed)",
                    )
                    n += 1
            except Exception as e:
                logger.warning("sweep_expired (dispatching) supabase failed: %s", e)
        return n

    def _expire(self, order_id: int, reason: str) -> None:
        try:
            self.update_status(
                order_id, PendingOrderStatus.EXPIRED, last_error=reason,
            )
        except Exception as e:
            logger.warning("sweep_expired _expire(%s) failed (%s)", order_id, e)


# ================================================================== #
# Postgres direct
# ================================================================== #
class PostgresPendingOrderQueue:
    def __init__(
        self, dsn: str, *, event_logger: EventLogger | None = None,
    ):
        self._dsn = dsn
        self._events = event_logger or NoOpEventLogger()

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def enqueue(self, order: PendingOrder) -> int:
        sql = (
            "insert into pending_orders "
            "(strategy_id, symbol, side, target_notional_usd, entry_price_ref, "
            " stop_loss_pct, take_profit_pct, mode, status, attempts, last_error, "
            " fused_signal_id, client_order_id, created_at, updated_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now()) "
            "on conflict (client_order_id) do update set "
            "  updated_at = now() "
            "returning id, (xmax = 0) as inserted"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                order.strategy_id, order.symbol, order.side,
                order.target_notional_usd, order.entry_price_ref,
                order.stop_loss_pct, order.take_profit_pct,
                order.mode, order.status.value, order.attempts, order.last_error,
                order.fused_signal_id, order.client_order_id,
            ))
            row = cur.fetchone()
            new_id = int(row[0])
            inserted = bool(row[1])
            conn.commit()
        order.id = new_id
        # Only log enqueue event for genuinely-new rows; idempotent retries
        # of the same client_order_id are not state changes.
        if inserted:
            _safe_log_event(
                self._events, new_id, None, PendingOrderStatus.PENDING,
                reason="initial enqueue",
                detail={"strategy_id": order.strategy_id, "symbol": order.symbol,
                        "side": order.side, "mode": order.mode,
                        "notional_usd": float(order.target_notional_usd)},
            )
        return new_id

    def get(self, order_id: int) -> PendingOrder:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select id, strategy_id, symbol, side, target_notional_usd, "
                "entry_price_ref, stop_loss_pct, take_profit_pct, mode, status, "
                "attempts, last_error, fused_signal_id, client_order_id, "
                "created_at, updated_at, dispatched_at, completed_at "
                "from pending_orders where id = %s",
                (order_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise PendingOrderNotFound(order_id)
        return _pg_row_to_order(row)

    def claim_next_pending(self, mode: ExecutionMode) -> PendingOrder | None:
        """SELECT FOR UPDATE SKIP LOCKED — safe for concurrent workers."""
        sql = (
            "with c as ("
            "  select id from pending_orders "
            "  where status = 'pending' and mode = %s "
            "  order by created_at "
            "  for update skip locked limit 1"
            ") "
            "update pending_orders "
            "set status='dispatching', attempts=attempts+1, "
            "    dispatched_at=now(), updated_at=now() "
            "where id in (select id from c) "
            "returning id, strategy_id, symbol, side, target_notional_usd, "
            "  entry_price_ref, stop_loss_pct, take_profit_pct, mode, status, "
            "  attempts, last_error, fused_signal_id, client_order_id, "
            "  created_at, updated_at, dispatched_at, completed_at"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (mode,))
            row = cur.fetchone()
            conn.commit()
        if not row:
            return None
        order = _pg_row_to_order(row)
        _safe_log_event(
            self._events, order.id,
            PendingOrderStatus.PENDING, PendingOrderStatus.DISPATCHING,
            reason="claimed by worker",
            detail={"attempt": order.attempts},
        )
        return order

    def update_status(
        self,
        order_id: int,
        status: PendingOrderStatus,
        *,
        last_error: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        terminal = status.value in {"filled", "rejected", "cancelled", "expired"}
        # Read current status BEFORE the update so the event has from_status.
        # Single-trip alternative: a CTE returning the old status, but this
        # path is plenty fast (the row is hot in cache from the worker's
        # claim).
        from_status: PendingOrderStatus | None = None
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "select status from pending_orders where id = %s", (order_id,),
            )
            r = cur.fetchone()
            if r is not None:
                from_status = PendingOrderStatus(r[0])
            update_sql = (
                "update pending_orders set "
                "  status = %s, "
                f"  attempts = attempts + {1 if increment_attempts else 0}, "
                "  last_error = coalesce(%s, last_error), "
                "  updated_at = now()"
                f"  {', completed_at = now()' if terminal else ''} "
                "where id = %s"
            )
            cur.execute(update_sql, (status.value, last_error, order_id))
            conn.commit()
        if from_status != status:
            _safe_log_event(
                self._events, order_id, from_status, status,
                reason=last_error,
            )

    def list_recent(
        self,
        *,
        limit: int = 100,
        status: PendingOrderStatus | None = None,
    ) -> list[PendingOrder]:
        if status is None:
            sql = (
                "select id, strategy_id, symbol, side, target_notional_usd, "
                "entry_price_ref, stop_loss_pct, take_profit_pct, mode, status, "
                "attempts, last_error, fused_signal_id, client_order_id, "
                "created_at, updated_at, dispatched_at, completed_at "
                "from pending_orders order by created_at desc limit %s"
            )
            params: tuple = (limit,)
        else:
            sql = (
                "select id, strategy_id, symbol, side, target_notional_usd, "
                "entry_price_ref, stop_loss_pct, take_profit_pct, mode, status, "
                "attempts, last_error, fused_signal_id, client_order_id, "
                "created_at, updated_at, dispatched_at, completed_at "
                "from pending_orders where status = %s "
                "order by created_at desc limit %s"
            )
            params = (status.value, limit)
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [_pg_row_to_order(r) for r in rows]

    def sweep_expired(
        self,
        *,
        pending_max_age_sec: float = 0,
        dispatching_max_age_sec: float = 0,
    ) -> int:
        """Single-row UPDATE per expiring order so each transition gets its
        own audit event with its own timestamp + reason. Bulk UPDATE would
        be faster but lose the per-row reason that downstream debugging
        relies on."""
        n = 0
        candidates: list[tuple[int, str]] = []

        with self._conn() as conn, conn.cursor() as cur:
            if pending_max_age_sec > 0:
                cur.execute(
                    "select id, "
                    "  extract(epoch from (now() - created_at)) as age_sec "
                    "from pending_orders "
                    "where status = 'pending' "
                    "  and created_at < now() - interval '%s seconds'",
                    (int(pending_max_age_sec),),
                )
                for oid, age in cur.fetchall():
                    candidates.append((
                        int(oid),
                        f"pending {float(age):.1f}s ≥ {pending_max_age_sec:.0f}s threshold",
                    ))

            if dispatching_max_age_sec > 0:
                cur.execute(
                    "select id, "
                    "  extract(epoch from (now() - coalesce(dispatched_at, updated_at))) "
                    "    as age_sec "
                    "from pending_orders "
                    "where status = 'dispatching' "
                    "  and coalesce(dispatched_at, updated_at) < "
                    "      now() - interval '%s seconds'",
                    (int(dispatching_max_age_sec),),
                )
                for oid, age in cur.fetchall():
                    candidates.append((
                        int(oid),
                        f"dispatching {float(age):.1f}s ≥ {dispatching_max_age_sec:.0f}s "
                        f"threshold (worker likely crashed)",
                    ))

        for order_id, reason in candidates:
            try:
                self.update_status(
                    order_id, PendingOrderStatus.EXPIRED, last_error=reason,
                )
                n += 1
            except Exception as e:
                logger.warning(
                    "sweep_expired: update_status(%s, EXPIRED) failed (%s)",
                    order_id, e,
                )
        return n


# ================================================================== #
# Helpers + factory
# ================================================================== #
def _row_to_order(row: dict[str, Any]) -> PendingOrder:
    return PendingOrder(
        id=int(row["id"]) if row.get("id") is not None else None,
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        side=row["side"],
        target_notional_usd=float(row["target_notional_usd"]),
        entry_price_ref=(float(row["entry_price_ref"]) if row.get("entry_price_ref") is not None else None),
        stop_loss_pct=(float(row["stop_loss_pct"]) if row.get("stop_loss_pct") is not None else None),
        take_profit_pct=(float(row["take_profit_pct"]) if row.get("take_profit_pct") is not None else None),
        mode=row["mode"],
        status=PendingOrderStatus(row["status"]),
        attempts=int(row.get("attempts") or 0),
        last_error=row.get("last_error"),
        fused_signal_id=row.get("fused_signal_id"),
        client_order_id=row.get("client_order_id"),
        created_at=_parse_iso(row.get("created_at")),
        updated_at=_parse_iso(row.get("updated_at")),
        dispatched_at=_parse_iso(row.get("dispatched_at")),
        completed_at=_parse_iso(row.get("completed_at")),
    )


def _pg_row_to_order(row: tuple) -> PendingOrder:
    return PendingOrder(
        id=int(row[0]),
        strategy_id=row[1], symbol=row[2], side=row[3],
        target_notional_usd=float(row[4]),
        entry_price_ref=(float(row[5]) if row[5] is not None else None),
        stop_loss_pct=(float(row[6]) if row[6] is not None else None),
        take_profit_pct=(float(row[7]) if row[7] is not None else None),
        mode=row[8], status=PendingOrderStatus(row[9]),
        attempts=int(row[10] or 0), last_error=row[11],
        fused_signal_id=row[12], client_order_id=row[13],
        created_at=row[14], updated_at=row[15],
        dispatched_at=row[16], completed_at=row[17],
    )


def _parse_iso(s: str | None) -> datetime | None:
    if s is None:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_queue(settings) -> PendingOrderQueue:  # noqa: ANN001
    """Factory mirroring signals.history priority. Wires a matching
    EventLogger automatically (round 36) so every status transition lands
    in `pending_order_events`."""
    events = build_event_logger(settings)
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("pending order queue: PostgresPendingOrderQueue (events=PG)")
        return PostgresPendingOrderQueue(dsn, event_logger=events)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("pending order queue: SupabasePendingOrderQueue (events=Supabase)")
            return SupabasePendingOrderQueue(client, event_logger=events)
        except ImportError:
            logger.warning("pending order queue: supabase-py missing")

    logger.warning("pending order queue: NoOp (no DB configured)")
    return NoOpPendingOrderQueue()


__all__ = [
    "PendingOrderNotFound",
    "PendingOrderQueue",
    "NoOpPendingOrderQueue",
    "InMemoryPendingOrderQueue",
    "SupabasePendingOrderQueue",
    "PostgresPendingOrderQueue",
    "build_queue",
]
