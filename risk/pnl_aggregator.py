"""PnLAggregator — sum realised PnL across paper / live trade tables.

G8 DailyLossCB needs `realised_today` to decide whether to halt trading.
Future guards (G9 ConsecutiveLoss, G10 Kelly) need similar aggregations
over different windows (last N days, win/loss split). Centralising here
keeps the SQL in one place.

Backends:
  - NoOp                  — always returns 0 (when DB not configured)
  - InMemory              — for tests + smoke
  - Supabase REST         — sums sm_paper_trades.pnl + live_trades.pnl
  - Postgres direct       — same with single SQL

`day_boundary_utc(now)` picks UTC midnight as the "today" cutoff. Phase
G v2 may add a configurable boundary (e.g. NY close 4pm ET) for stocks.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class PnLAggregator(Protocol):
    def realised_today_usd(self, *, now: datetime | None = None) -> float: ...
    def realised_window_usd(
        self, *, hours: int, now: datetime | None = None,
    ) -> float: ...


def day_boundary_utc(now: datetime | None = None) -> datetime:
    """Today's UTC midnight (00:00) for `now` (defaults to now())."""
    n = now or datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0)


# ================================================================== #
# NoOp
# ================================================================== #
class NoOpPnLAggregator:
    """Always returns 0. Use when no DB / no realised trades yet — G8
    won't fire and CB stays open. Caller should swap in real backend
    before live mode."""

    def realised_today_usd(self, *, now: datetime | None = None) -> float:
        return 0.0

    def realised_window_usd(
        self, *, hours: int, now: datetime | None = None,
    ) -> float:
        return 0.0


# ================================================================== #
# InMemory — for tests
# ================================================================== #
class InMemoryPnLAggregator:
    """Caller pre-loads `(closed_at, pnl_usd)` pairs; aggregator sums
    those whose closed_at >= window start."""

    def __init__(self, trades: list[tuple[datetime, float]] | None = None):
        # Normalise to UTC-aware
        self._trades: list[tuple[datetime, float]] = []
        for ts, pnl in (trades or []):
            self.add(ts, pnl)

    def add(self, closed_at: datetime, pnl_usd: float) -> None:
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=timezone.utc)
        self._trades.append((closed_at.astimezone(timezone.utc), float(pnl_usd)))

    def realised_today_usd(self, *, now: datetime | None = None) -> float:
        cutoff = day_boundary_utc(now)
        return sum(p for ts, p in self._trades if ts >= cutoff)

    def realised_window_usd(
        self, *, hours: int, now: datetime | None = None,
    ) -> float:
        n = now or datetime.now(timezone.utc)
        cutoff = n - timedelta(hours=hours)
        return sum(p for ts, p in self._trades if ts >= cutoff)


# ================================================================== #
# Supabase REST
# ================================================================== #
class SupabasePnLAggregator:
    """Sums pnl from sm_paper_trades + live_trades since cutoff.

    Both tables expose `pnl numeric` (nullable; NULL = still open) and
    `closed_at timestamptz`. Only closed trades count toward realised.
    """

    PAPER_TABLE = "sm_paper_trades"
    LIVE_TABLE = "live_trades"

    def __init__(self, client: Any, *, include_live: bool = True):
        self._client = client
        self._include_live = include_live

    def realised_today_usd(self, *, now: datetime | None = None) -> float:
        return self._sum_since(day_boundary_utc(now))

    def realised_window_usd(
        self, *, hours: int, now: datetime | None = None,
    ) -> float:
        n = now or datetime.now(timezone.utc)
        return self._sum_since(n - timedelta(hours=hours))

    def _sum_since(self, since: datetime) -> float:
        iso = since.astimezone(timezone.utc).isoformat()
        total = 0.0

        for tbl in self._tables():
            try:
                res = (
                    self._client.table(tbl).select("pnl,closed_at")
                    .gte("closed_at", iso)
                    .not_.is_("pnl", "null")
                    .execute()
                )
                total += sum(
                    float(r["pnl"]) for r in (res.data or [])
                    if r.get("pnl") is not None
                )
            except Exception as e:
                logger.warning(
                    "PnL aggregator: %s query failed (%s) — treating as 0",
                    tbl, e,
                )

        return total

    def _tables(self) -> list[str]:
        return [self.PAPER_TABLE, self.LIVE_TABLE] if self._include_live else [self.PAPER_TABLE]


# ================================================================== #
# Postgres direct
# ================================================================== #
class PostgresPnLAggregator:
    def __init__(self, dsn: str, *, include_live: bool = True):
        self._dsn = dsn
        self._include_live = include_live

    def _conn(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def realised_today_usd(self, *, now: datetime | None = None) -> float:
        return self._sum_since(day_boundary_utc(now))

    def realised_window_usd(
        self, *, hours: int, now: datetime | None = None,
    ) -> float:
        n = now or datetime.now(timezone.utc)
        return self._sum_since(n - timedelta(hours=hours))

    def _sum_since(self, since: datetime) -> float:
        ts = since.astimezone(timezone.utc)
        sql_paper = (
            "select coalesce(sum(pnl), 0) "
            "from sm_paper_trades "
            "where closed_at >= %s and pnl is not null"
        )
        sql_live = (
            "select coalesce(sum(pnl), 0) "
            "from live_trades "
            "where closed_at >= %s and pnl is not null"
        )

        total = 0.0
        with self._conn() as conn, conn.cursor() as cur:
            try:
                cur.execute(sql_paper, (ts,))
                total += float(cur.fetchone()[0] or 0)
            except Exception as e:
                logger.warning("PnL paper query failed: %s", e)

            if self._include_live:
                try:
                    cur.execute(sql_live, (ts,))
                    total += float(cur.fetchone()[0] or 0)
                except Exception as e:
                    logger.warning("PnL live query failed: %s", e)
        return total


# ================================================================== #
# Factory
# ================================================================== #
def build_pnl_aggregator(settings) -> PnLAggregator:  # noqa: ANN001
    """Pick best backend. Mirrors signals.history priority."""
    dsn = (getattr(settings, "database_url", "") or "").strip()
    if dsn:
        logger.info("pnl_aggregator: PostgresPnLAggregator")
        return PostgresPnLAggregator(dsn)

    sb_url = (getattr(settings, "supabase_url", "") or "").strip()
    sb_key = (getattr(settings, "supabase_service_key", "") or "").strip()
    if sb_url and sb_key:
        try:
            from supabase import create_client
            client = create_client(sb_url, sb_key)
            logger.info("pnl_aggregator: SupabasePnLAggregator")
            return SupabasePnLAggregator(client)
        except ImportError:
            logger.warning("pnl_aggregator: supabase-py missing")

    logger.warning(
        "pnl_aggregator: NoOp (no DB configured) — G8 DailyLossCB will not fire"
    )
    return NoOpPnLAggregator()


__all__ = [
    "PnLAggregator",
    "NoOpPnLAggregator",
    "InMemoryPnLAggregator",
    "SupabasePnLAggregator",
    "PostgresPnLAggregator",
    "build_pnl_aggregator",
    "day_boundary_utc",
]
