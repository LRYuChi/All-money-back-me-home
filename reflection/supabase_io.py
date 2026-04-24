"""Supabase + Postgres implementations of SignalHistoryReader / Updater.

The validator core (reflection/validator.py) declares Protocols.
This module provides concrete backends so the validator can run against
prod data. Keeps SQL/REST details out of the core.

Two flavours:
  * SupabaseSignalHistoryReader/Updater  — REST via supabase-py
  * PostgresSignalHistoryReader/Updater  — direct psycopg, faster bulk

`build_reader_updater(settings)` mirrors `shared.signals.history.build_writer`
priority: DATABASE_URL → Postgres > SUPABASE_URL → REST > raise.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from reflection.validator import (
    SignalHistoryReader,
    SignalHistoryUpdater,
    UnvalidatedRow,
)

logger = logging.getLogger(__name__)


# ================================================================== #
# Supabase REST backend
# ================================================================== #
class SupabaseSignalHistoryReader:
    """Reads unvalidated rows via supabase-py REST.

    Filter:
      validated_at IS NULL
      AND ts <= now() - INTERVAL '<min_age_seconds>'
      AND ts >= now() - INTERVAL '<max_age_hours>'   (don't bother with ancient)
    Ordered ts ASC so oldest validate first.
    """

    TABLE = "signal_history"

    def __init__(self, client: Any, *, min_horizon_seconds: int = 900):
        # min_horizon_seconds = 15min by default — anything younger has no
        # forward window for our shortest horizon (15m). Reader filters
        # this so validator doesn't waste round trips.
        self._client = client
        self._min_horizon_sec = min_horizon_seconds

    def read_unvalidated(
        self, *, max_age_hours: int, limit: int
    ) -> Iterable[UnvalidatedRow]:
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(hours=max_age_hours)
        ready = now - timedelta(seconds=self._min_horizon_sec)

        res = (
            self._client.table(self.TABLE)
            .select("id,symbol,horizon,direction,ts,expires_at")
            .is_("validated_at", "null")
            .gte("ts", oldest.isoformat())
            .lte("ts", ready.isoformat())
            .order("ts")
            .limit(limit)
            .execute()
        )

        rows = []
        for r in (res.data or []):
            try:
                rows.append(_supabase_row_to_unvalidated(r))
            except Exception as e:
                logger.warning(
                    "reader: skipping malformed row id=%s: %s", r.get("id"), e,
                )
        return rows


class SupabaseSignalHistoryUpdater:
    """Writes verdicts via supabase-py REST."""

    TABLE = "signal_history"

    def __init__(self, client: Any):
        self._client = client

    def update_verdict(
        self,
        signal_id: int,
        *,
        was_correct: bool | None,
        actual_return_pct: float | None,
        validated_at: datetime,
    ) -> None:
        payload: dict[str, Any] = {
            "was_correct": was_correct,
            "actual_return_pct": actual_return_pct,
            "validated_at": validated_at.astimezone(timezone.utc).isoformat(),
        }
        self._client.table(self.TABLE).update(payload).eq("id", signal_id).execute()


# ================================================================== #
# Postgres direct backend
# ================================================================== #
class PostgresSignalHistoryReader:
    """Reads via direct psycopg connection. Faster on large batches."""

    def __init__(self, dsn: str, *, min_horizon_seconds: int = 900):
        self._dsn = dsn
        self._min_horizon_sec = min_horizon_seconds

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def read_unvalidated(
        self, *, max_age_hours: int, limit: int
    ) -> Iterable[UnvalidatedRow]:
        sql = (
            "select id, symbol, horizon, direction, ts, expires_at "
            "from signal_history "
            "where validated_at is null "
            "  and ts >= now() - make_interval(hours => %s) "
            "  and ts <= now() - make_interval(secs => %s) "
            "order by ts asc "
            "limit %s"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (max_age_hours, self._min_horizon_sec, limit))
            rows = cur.fetchall()
        return [
            UnvalidatedRow(
                id=int(r[0]), symbol=r[1], horizon=r[2], direction=r[3],
                ts=r[4], expires_at=r[5],
            )
            for r in rows
        ]


class PostgresSignalHistoryUpdater:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def update_verdict(
        self,
        signal_id: int,
        *,
        was_correct: bool | None,
        actual_return_pct: float | None,
        validated_at: datetime,
    ) -> None:
        sql = (
            "update signal_history set "
            "was_correct = %s, actual_return_pct = %s, validated_at = %s "
            "where id = %s"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                was_correct, actual_return_pct,
                validated_at.astimezone(timezone.utc),
                signal_id,
            ))
            conn.commit()


# ================================================================== #
# Helpers + factory
# ================================================================== #
def _supabase_row_to_unvalidated(row: dict[str, Any]) -> UnvalidatedRow:
    return UnvalidatedRow(
        id=int(row["id"]),
        symbol=row["symbol"],
        horizon=row["horizon"],
        direction=row["direction"],
        ts=_parse_iso(row["ts"]),
        expires_at=_parse_iso(row["expires_at"]),
    )


def _parse_iso(s: str) -> datetime:
    """Parse ISO 8601 (Supabase REST returns trailing Z); always return UTC-aware."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def build_reader_updater(
    settings,  # noqa: ANN001
) -> tuple[SignalHistoryReader, SignalHistoryUpdater]:
    """Pick best backend pair given settings.

    Priority:
      1. DATABASE_URL set → PostgresReader + PostgresUpdater (fastest)
      2. SUPABASE_URL + SUPABASE_SERVICE_KEY → SupabaseReader + SupabaseUpdater
      3. raise — there's no useful fallback for prod reflection
    """
    dsn = getattr(settings, "database_url", "") or ""
    if dsn:
        logger.info("reflection IO: PostgresReader + PostgresUpdater")
        return (
            PostgresSignalHistoryReader(dsn),
            PostgresSignalHistoryUpdater(dsn),
        )

    sb_url = getattr(settings, "supabase_url", "") or ""
    sb_key = getattr(settings, "supabase_service_key", "") or ""
    if sb_url and sb_key:
        from supabase import create_client
        client = create_client(sb_url, sb_key)
        logger.info("reflection IO: SupabaseReader + SupabaseUpdater")
        return (
            SupabaseSignalHistoryReader(client),
            SupabaseSignalHistoryUpdater(client),
        )

    raise RuntimeError(
        "reflection IO: neither DATABASE_URL nor SUPABASE_URL+KEY set — "
        "validator cannot run against real signal_history",
    )


__all__ = [
    "SupabaseSignalHistoryReader",
    "SupabaseSignalHistoryUpdater",
    "PostgresSignalHistoryReader",
    "PostgresSignalHistoryUpdater",
    "build_reader_updater",
]
