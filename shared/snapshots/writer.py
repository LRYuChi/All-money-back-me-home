"""Snapshot writers — Protocol + 4 implementations + factory.

Mirrors `shared.signals.history.SignalHistoryWriter` design:
  - NoOp / InMemory / Supabase / Postgres
  - `record_safe()` swallows errors so a failed snapshot write never
    breaks a backtest job
  - `build_writer(settings)` priority: DATABASE_URL → Postgres
    > SUPABASE_URL+KEY → Supabase > NoOp
"""
from __future__ import annotations

import logging
from typing import Any, Protocol

from shared.snapshots.types import BacktestSnapshot

logger = logging.getLogger(__name__)


class BacktestSnapshotWriter(Protocol):
    """Persists snapshots. record() returns the new id (0 for NoOp)."""

    def record(self, snapshot: BacktestSnapshot) -> int: ...


def record_safe(
    writer: BacktestSnapshotWriter, snapshot: BacktestSnapshot
) -> int | None:
    """Try to write; on failure log and return None. Backtests never fail
    because snapshot persistence is unavailable."""
    try:
        return writer.record(snapshot)
    except Exception as e:
        logger.warning(
            "snapshot write failed (%s): %s — continuing", type(e).__name__, e,
        )
        return None


# ================================================================== #
# Implementations
# ================================================================== #
class NoOpSnapshotWriter:
    """Discard. Used when nothing is configured."""

    def record(self, snapshot: BacktestSnapshot) -> int:
        logger.info(
            "snapshot (no-op): kind=%s n_trades=%s pass=%s",
            snapshot.kind, snapshot.n_trades, snapshot.decision_pass,
        )
        return 0


class InMemorySnapshotWriter:
    """Keeps records in a list. For tests + smoke runs."""

    def __init__(self) -> None:
        self.records: list[BacktestSnapshot] = []
        self._next_id: int = 1

    def record(self, snapshot: BacktestSnapshot) -> int:
        snapshot.id = self._next_id
        self._next_id += 1
        self.records.append(snapshot)
        return snapshot.id

    def by_kind(self, kind: str) -> list[BacktestSnapshot]:
        return [s for s in self.records if s.kind == kind]


class SupabaseSnapshotWriter:
    """REST insert via supabase-py."""

    TABLE = "backtest_snapshots"

    def __init__(self, client: Any) -> None:
        self._client = client

    def record(self, snapshot: BacktestSnapshot) -> int:
        res = self._client.table(self.TABLE).insert(snapshot.to_row()).execute()
        new_id = int(res.data[0]["id"]) if res.data else 0
        snapshot.id = new_id
        return new_id


class PostgresSnapshotWriter:
    """Direct psycopg insert. Faster + supports RETURNING clause."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def record(self, snapshot: BacktestSnapshot) -> int:
        import json as _json
        row = snapshot.to_row()
        sql = (
            "insert into backtest_snapshots "
            "(kind, git_commit, git_dirty, config, cutoffs, data_window, "
            " rng_seed, report, decision_pass, decision_reason, "
            " n_trades, median_pnl_pct, max_drawdown, created_at) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "returning id"
        )
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, (
                row["kind"], row["git_commit"], row["git_dirty"],
                _json.dumps(row["config"]),
                _json.dumps(row["cutoffs"]) if row["cutoffs"] else None,
                _json.dumps(row["data_window"]) if row["data_window"] else None,
                row["rng_seed"],
                _json.dumps(row["report"]),
                row["decision_pass"], row["decision_reason"],
                row["n_trades"], row["median_pnl_pct"], row["max_drawdown"],
                row["created_at"],
            ))
            new_id = int(cur.fetchone()[0])
            conn.commit()
        snapshot.id = new_id
        return new_id


# ================================================================== #
# Factory
# ================================================================== #
def build_writer(settings) -> BacktestSnapshotWriter:  # noqa: ANN001
    """Pick best writer given settings. Mirrors signals.history.build_writer."""
    dsn = getattr(settings, "database_url", "") or ""
    if dsn:
        logger.info("snapshot writer: PostgresSnapshotWriter")
        return PostgresSnapshotWriter(dsn)

    sb_url = getattr(settings, "supabase_url", "") or ""
    sb_key = getattr(settings, "supabase_service_key", "") or ""
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("snapshot writer: SupabaseSnapshotWriter")
            return SupabaseSnapshotWriter(client)
        except ImportError:
            logger.warning("snapshot: supabase-py not installed, falling back to NoOp")

    logger.info("snapshot writer: NoOp (no DB configured)")
    return NoOpSnapshotWriter()


__all__ = [
    "BacktestSnapshotWriter",
    "NoOpSnapshotWriter",
    "InMemorySnapshotWriter",
    "SupabaseSnapshotWriter",
    "PostgresSnapshotWriter",
    "build_writer",
    "record_safe",
]
