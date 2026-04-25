"""Audit hook for credential read/write/delete events (round 34).

Round 7 shipped migration 018 with `secret_access_log`; this module wires
the writes. Each store call → one audit row with (name, op, actor, success,
notes). Plaintext is NEVER logged — just metadata.

Hook is fire-and-forget: audit failures log + swallow. Reasoning: a flaky
audit DB must not block credential reads, which would block trading. The
audit gap is visible in the `success`-vs-`call-count` discrepancy.

Backends mirror credential store: NoOp / Postgres / Supabase / InMemory.
A `build_audit_hook(settings)` factory returns the matching hook for a
given store; `build_store` (in store.py) calls it automatically.

Actor resolution priority:
    1. Explicit `actor=` arg passed to write()/read()/delete()
    2. `with_actor("...")` context manager (thread-local)
    3. `CREDENTIAL_ACTOR` env var
    4. None (recorded as NULL — searchable for "unknown caller")
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol

logger = logging.getLogger(__name__)


AuditOp = Literal["read", "write", "delete", "rotate"]


@dataclass(slots=True, frozen=True)
class AuditEvent:
    """Returned by `history()` queries — one row from secret_access_log."""

    name: str
    op: AuditOp
    actor: str | None
    success: bool
    notes: str | None
    created_at: datetime


class AuditHook(Protocol):
    def record(
        self,
        name: str,
        op: AuditOp,
        *,
        actor: str | None = None,
        success: bool = True,
        notes: str | None = None,
    ) -> None: ...

    def history(self, name: str, *, limit: int = 50) -> list[AuditEvent]: ...


# ================================================================== #
# Actor context — thread-local override
# ================================================================== #
_current_actor: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "credential_actor", default=None,
)


@contextmanager
def with_actor(actor: str) -> Iterator[None]:
    """Set the audit actor for everything inside this block.

    Useful at daemon startup:

        with with_actor("daemon:smart_money_shadow"):
            run_forever(...)

    All credential reads inside `run_forever` get tagged with the daemon
    name automatically.
    """
    token = _current_actor.set(actor)
    try:
        yield
    finally:
        _current_actor.reset(token)


def resolve_actor(explicit: str | None = None) -> str | None:
    """Resolution chain: explicit → context var → env → None."""
    if explicit is not None:
        return explicit
    ctx = _current_actor.get()
    if ctx is not None:
        return ctx
    env = (os.environ.get("CREDENTIAL_ACTOR", "") or "").strip()
    return env or None


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpAuditHook:
    """Discards all events. Default when no DB is configured."""

    def record(
        self, name: str, op: AuditOp, *,
        actor: str | None = None, success: bool = True,
        notes: str | None = None,
    ) -> None:
        pass

    def history(self, name: str, *, limit: int = 50) -> list[AuditEvent]:
        return []


# ================================================================== #
# InMemory — for tests
# ================================================================== #
class InMemoryAuditHook:
    """Caller can inspect `events` for assertions."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(
        self, name: str, op: AuditOp, *,
        actor: str | None = None, success: bool = True,
        notes: str | None = None,
    ) -> None:
        self.events.append(AuditEvent(
            name=name, op=op, actor=resolve_actor(actor),
            success=success, notes=notes,
            created_at=datetime.now(timezone.utc),
        ))

    def history(self, name: str, *, limit: int = 50) -> list[AuditEvent]:
        rows = [e for e in self.events if e.name == name]
        rows.sort(key=lambda e: e.created_at, reverse=True)
        return rows[:limit]


# ================================================================== #
# Postgres
# ================================================================== #
class PostgresAuditHook:
    TABLE = "secret_access_log"

    def __init__(self, dsn: str):
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def record(
        self, name: str, op: AuditOp, *,
        actor: str | None = None, success: bool = True,
        notes: str | None = None,
    ) -> None:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"insert into {self.TABLE} "
                    "(name, op, actor, success, notes, created_at) "
                    "values (%s, %s, %s, %s, %s, now())",
                    (name, op, resolve_actor(actor), success, notes),
                )
                conn.commit()
        except Exception as e:
            # Fire-and-forget — audit gap > blocking the operation
            logger.warning(
                "credential_audit: PG insert failed (%s): %s/%s/%s",
                e, name, op, actor,
            )

    def history(self, name: str, *, limit: int = 50) -> list[AuditEvent]:
        try:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    f"select name, op, actor, success, notes, created_at "
                    f"from {self.TABLE} where name = %s "
                    f"order by created_at desc limit %s",
                    (name, limit),
                )
                rows = cur.fetchall()
        except Exception as e:
            logger.warning("credential_audit: PG history failed: %s", e)
            return []
        return [
            AuditEvent(
                name=r[0], op=r[1], actor=r[2], success=r[3], notes=r[4],
                created_at=r[5],
            )
            for r in rows
        ]


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabaseAuditHook:
    TABLE = "secret_access_log"

    def __init__(self, client: Any):
        self._client = client

    def record(
        self, name: str, op: AuditOp, *,
        actor: str | None = None, success: bool = True,
        notes: str | None = None,
    ) -> None:
        try:
            self._client.table(self.TABLE).insert({
                "name": name,
                "op": op,
                "actor": resolve_actor(actor),
                "success": success,
                "notes": notes,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
        except Exception as e:
            logger.warning(
                "credential_audit: Supabase insert failed (%s): %s/%s/%s",
                e, name, op, actor,
            )

    def history(self, name: str, *, limit: int = 50) -> list[AuditEvent]:
        try:
            res = (
                self._client.table(self.TABLE).select("*")
                .eq("name", name).order("created_at", desc=True)
                .limit(limit).execute()
            )
        except Exception as e:
            logger.warning("credential_audit: Supabase history failed: %s", e)
            return []
        return [_row_to_event(r) for r in (res.data or [])]


def _row_to_event(row: dict) -> AuditEvent:
    return AuditEvent(
        name=row["name"], op=row["op"], actor=row.get("actor"),
        success=bool(row["success"]), notes=row.get("notes"),
        created_at=_parse_iso(row.get("created_at"))
                   or datetime.now(timezone.utc),
    )


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ================================================================== #
# Factory
# ================================================================== #
def build_audit_hook(settings) -> AuditHook:  # noqa: ANN001
    """Pick the audit hook matching the credential store backend."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        return PostgresAuditHook(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            return SupabaseAuditHook(create_client(sb_url, sb_key))
        except ImportError:
            pass

    return NoOpAuditHook()


__all__ = [
    "AuditEvent",
    "AuditHook",
    "AuditOp",
    "InMemoryAuditHook",
    "NoOpAuditHook",
    "PostgresAuditHook",
    "SupabaseAuditHook",
    "build_audit_hook",
    "resolve_actor",
    "with_actor",
]
