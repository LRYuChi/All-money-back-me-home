"""Persistence layer for signal_history table.

Design decisions
----------------
1. **Writer Protocol, not concrete class** — so tests inject InMemory and
   production injects Supabase/Postgres without changing call sites.

2. **record_safe() swallows errors** — signal_history is *observability*,
   not the primary pipeline. If the insert fails (migration not applied,
   DB blip, network hiccup), we log a warning and keep going. The shadow
   daemon must NEVER be brought down by dual-write failures.

3. **No background queue (yet)** — per R10 decision (batch 5s) is a Phase
   C/D optimisation. For now writes are synchronous per-signal; the
   Supabase REST call takes ~50-100ms which is acceptable at current
   signal rate (< 1/sec per daemon).

4. **Deferred SupabaseClient** — so `shared/` doesn't import supabase-py
   at module load. Writers are constructed by callers who already have
   a configured client.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from shared.signals.types import UniversalSignal

logger = logging.getLogger(__name__)


class SignalHistoryWriter(Protocol):
    """Writer interface — any implementation must accept UniversalSignal
    and persist it. `record()` may raise; callers should use `record_safe()`
    which wraps with a try/except."""

    def record(self, signal: UniversalSignal) -> None: ...


def record_safe(writer: SignalHistoryWriter, signal: UniversalSignal) -> bool:
    """Attempt to record; log and swallow any exception.

    Returns True if recorded successfully, False otherwise. Callers
    generally don't act on the result — it's for metrics only.
    """
    try:
        writer.record(signal)
        return True
    except Exception as e:
        logger.warning(
            "signal_history write failed (%s): %s — continuing",
            type(e).__name__, e,
        )
        return False


# ------------------------------------------------------------------ #
# Implementations
# ------------------------------------------------------------------ #
class NoOpSignalHistoryWriter:
    """Swallows everything. Use when signal_history is not wired (e.g.
    migration 016 not yet applied, or in ad-hoc CLI invocations)."""

    def record(self, signal: UniversalSignal) -> None:
        return


class InMemorySignalHistoryWriter:
    """Keeps records in a list. For tests + smoke runs."""

    def __init__(self) -> None:
        self.records: list[UniversalSignal] = []

    def record(self, signal: UniversalSignal) -> None:
        self.records.append(signal)

    def by_source(self, source_value: str) -> list[UniversalSignal]:
        return [s for s in self.records if s.source.value == source_value]


class SupabaseSignalHistoryWriter:
    """Writes via supabase-py client — the shadow daemon's primary backend."""

    TABLE = "signal_history"

    def __init__(self, client: Any) -> None:
        """client: supabase.Client instance."""
        self._client = client

    def record(self, signal: UniversalSignal) -> None:
        self._client.table(self.TABLE).insert(signal.to_row()).execute()


class PostgresSignalHistoryWriter:
    """Writes via direct psycopg connection — faster for batch workloads."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def record(self, signal: UniversalSignal) -> None:
        import json as _json
        row = signal.to_row()
        sql = (
            "insert into signal_history "
            "(source, symbol, horizon, direction, strength, reason, details, ts, expires_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                row["source"], row["symbol"], row["horizon"],
                row["direction"], row["strength"], row["reason"],
                _json.dumps(row["details"]),
                row["ts"], row["expires_at"],
            ))
            conn.commit()


# ------------------------------------------------------------------ #
# Factory — pick best writer given what's available
# ------------------------------------------------------------------ #
def build_writer(settings) -> SignalHistoryWriter:  # noqa: ANN001
    """Mirror smart_money.store.db.build_store's priority order:
       1. PostgresStore if DATABASE_URL set → PostgresSignalHistoryWriter
       2. Supabase REST if SUPABASE_URL+KEY set → SupabaseSignalHistoryWriter
       3. Fall through to NoOp (writes silently dropped)
    """
    try:
        dsn = getattr(settings, "database_url", "")
    except AttributeError:
        dsn = ""
    if dsn:
        logger.info("signal_history writer: PostgresSignalHistoryWriter")
        return PostgresSignalHistoryWriter(dsn)

    try:
        sb_url = getattr(settings, "supabase_url", "")
        sb_key = getattr(settings, "supabase_service_key", "")
    except AttributeError:
        sb_url = sb_key = ""

    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("signal_history writer: SupabaseSignalHistoryWriter")
            return SupabaseSignalHistoryWriter(client)
        except ImportError:
            logger.warning("signal_history: supabase-py not installed, falling back to NoOp")

    logger.warning("signal_history writer: NoOp (neither DATABASE_URL nor SUPABASE_URL configured)")
    return NoOpSignalHistoryWriter()


__all__ = [
    "SignalHistoryWriter",
    "NoOpSignalHistoryWriter",
    "InMemorySignalHistoryWriter",
    "SupabaseSignalHistoryWriter",
    "PostgresSignalHistoryWriter",
    "build_writer",
    "record_safe",
]
